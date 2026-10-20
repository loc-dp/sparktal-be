from __future__ import annotations

from typing import Any
from uuid import UUID

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import Count, Q, QuerySet
from drf_spectacular.utils import extend_schema, extend_schema_view
from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.invites import InviteError, send_password_invite
from apps.companies.access import company_of, membership_of
from apps.companies.home import home_payload
from apps.companies.members import (
    ACCOUNT_LOCKED,
    EMAIL_TAKEN,
    MemberRuleError,
    invite_member,
    remove_member,
    update_member,
)
from apps.companies.models import Company, CompanyMembership, CompanyRole, Team
from apps.companies.serializers import (
    CompanyCompleteSerializer,
    CompanyHomeSerializer,
    CompanySerializer,
    ImageUploadSerializer,
    MemberInviteSerializer,
    MemberSerializer,
    MemberUpdateSerializer,
    TeamSerializer,
    TeamWriteSerializer,
)
from apps.companies.teams import create_team, delete_team, rename_team, resolve_team
from apps.jobs.access import jobs_visible_to
from apps.jobs.models import Job

UPLOAD_REQUEST = {
    "multipart/form-data": {
        "type": "object",
        "properties": {"file": {"type": "string", "format": "binary"}},
        "required": ["file"],
    }
}

ACCEPTED_IMAGE_FORMATS = {"PNG", "JPEG", "WEBP"}
LIMITS_MB = {"logo": 2, "banner": 5}


def get_company_or_404(request: Request) -> Company:
    company = company_of(request.user)  # type: ignore[arg-type]
    if company is None:
        raise NotFound("You are not a member of a company.")
    return company


def require_change_permission(request: Request, company: Company) -> None:
    if not request.user.has_perm("companies.change_company", company):
        raise PermissionDenied("Only a Company Admin or Talent Acquisition can edit this page.")


def validate_image(file: UploadedFile[Any], kind: str) -> None:
    limit_mb = LIMITS_MB[kind]
    if file.size and file.size > limit_mb * 1024 * 1024:
        size_mb = (file.size or 0) / 1_048_576
        raise ValidationError(
            {"file": [f"This file is {size_mb:.1f} MB. The limit is {limit_mb} MB."]}
        )
    unsupported = {"file": [f"“{file.name}” isn't a supported image. Use PNG, JPG or WebP."]}
    try:
        image = Image.open(file)
        image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError(unsupported) from exc
    if (image.format or "").upper() not in ACCEPTED_IMAGE_FORMATS:
        raise ValidationError(unsupported)
    file.seek(0)


@extend_schema_view(
    get=extend_schema(operation_id="company_retrieve", tags=["companies"]),
    patch=extend_schema(operation_id="company_partial_update", tags=["companies"]),
)
class CompanyView(RetrieveUpdateAPIView[Company]):
    serializer_class = CompanySerializer
    http_method_names = ("get", "patch", "head", "options")

    def get_object(self) -> Company:
        company = get_company_or_404(self.request)
        if self.request.method == "PATCH":
            require_change_permission(self.request, company)
        return company


class CompanyCompleteView(APIView):
    @extend_schema(
        operation_id="company_complete_create",
        tags=["companies"],
        request=None,
        responses=CompanySerializer,
    )
    def post(self, request: Request) -> Response:
        company = get_company_or_404(request)
        require_change_permission(request, company)
        serializer = CompanyCompleteSerializer(data={}, context={"company": company})
        serializer.is_valid(raise_exception=True)
        company.mark_profile_completed()
        return Response(CompanySerializer(company, context={"request": request}).data)


class CompanyImageView(APIView):
    parser_classes = (MultiPartParser,)
    kind = "logo"

    def _respond(self, request: Request, company: Company) -> Response:
        return Response(CompanySerializer(company, context={"request": request}).data)

    def put(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        company = get_company_or_404(request)
        require_change_permission(request, company)
        serializer = ImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        file: UploadedFile[Any] = serializer.validated_data["file"]
        validate_image(file, self.kind)
        setattr(company, self.kind, file)
        company.save(update_fields=[self.kind, "updated_at"])
        return self._respond(request, company)

    def delete(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        company = get_company_or_404(request)
        require_change_permission(request, company)
        field = getattr(company, self.kind)
        if field:
            field.delete(save=False)
            setattr(company, self.kind, None)
            company.save(update_fields=[self.kind, "updated_at"])
        return self._respond(request, company)


@extend_schema_view(
    put=extend_schema(
        operation_id="company_logo_update",
        tags=["companies"],
        request=UPLOAD_REQUEST,
        responses=CompanySerializer,
    ),
    delete=extend_schema(
        operation_id="company_logo_destroy", tags=["companies"], responses=CompanySerializer
    ),
)
class CompanyLogoView(CompanyImageView):
    kind = "logo"


@extend_schema_view(
    put=extend_schema(
        operation_id="company_banner_update",
        tags=["companies"],
        request=UPLOAD_REQUEST,
        responses=CompanySerializer,
    ),
    delete=extend_schema(
        operation_id="company_banner_destroy", tags=["companies"], responses=CompanySerializer
    ),
)
class CompanyBannerView(CompanyImageView):
    kind = "banner"


@extend_schema_view(
    get=extend_schema(
        tags=["company"],
        operation_id="company_home_retrieve",
        responses=CompanyHomeSerializer,
    )
)
class CompanyHomeView(APIView):
    def get(self, request: Request) -> Response:
        get_company_or_404(request)
        return Response(home_payload(request.user))  # type: ignore[arg-type]


class InviteEmailFailed(APIException):
    status_code = 502
    default_detail = "We couldn't send the invite email, so nothing was saved. Please try again."
    default_code = "invite_email_failed"


def send_invite(membership: CompanyMembership, *, invited_by: str | None) -> None:
    """Email the set-password invite now, inside the caller's transaction.

    A failure raises `InviteEmailFailed` (502), so everything the caller saved rolls back.
    """
    try:
        send_password_invite(
            membership.user.email,
            company_name=membership.company.name,
            role_label=membership.get_role_display(),
            invited_by=invited_by,
        )
    except InviteError as exc:
        raise InviteEmailFailed() from exc


def members_visible_to(request: Request, company: Company) -> QuerySet[CompanyMembership]:
    """An admin sees everyone in the company; anyone else only their own team."""
    queryset = CompanyMembership.objects.filter(company=company)
    membership = membership_of(request.user)  # type: ignore[arg-type]
    if membership is None:
        return queryset.none()
    if membership.role == CompanyRole.ADMIN:
        return queryset
    if membership.team_id is None:
        return queryset.none()
    return queryset.filter(team_id=membership.team_id)


def require_manage_members(request: Request, company: Company) -> None:
    if not request.user.has_perm("companies.manage_members", company):
        raise PermissionDenied("Only a Company Admin can manage the team.")


def team_queryset(company: Company, counted_jobs: QuerySet[Job]) -> QuerySet[Team]:
    """Teams with their headcount and how many of `counted_jobs` are theirs."""
    return Team.objects.filter(company=company).annotate(
        member_count=Count("memberships", distinct=True),
        job_count=Count("jobs", filter=Q(jobs__in=counted_jobs), distinct=True),
    )


class TeamScope:
    # Where the member and team views look, and who may change what they find there.
    #
    # On the company side every answer comes from the signed-in member's own seat. The Sparktal
    # ops portal subclasses each view with `apps.ops.api.OpsCompanyScope`, which overrides every
    # hook below: the company comes from the URL, staff see all of it, and `IsSparktalStaff` has
    # already decided who may act. (Comments, not a docstring: drf-spectacular would show a
    # docstring as the description of every view that inherits this.)

    # What a Sparktal lock is called. Inviting a removed member Sparktal locked reads as a taken
    # address, so no company learns of a lock it did not see; resending to your own locked
    # member (you see its Locked pill) says so, without "unlock it": only Sparktal can.
    locked_invite_detail: str = EMAIL_TAKEN
    locked_resend_detail: str = ACCOUNT_LOCKED

    def company_for(self, request: Request, **kwargs: Any) -> Company:
        """The company the request is about; `kwargs` are the URL's."""
        return get_company_or_404(request)

    def require_manage(self, request: Request, company: Company) -> None:
        """403 unless the user may manage the company's members and teams."""
        require_manage_members(request, company)

    def visible_members(self, request: Request, company: Company) -> QuerySet[CompanyMembership]:
        """The members the user may see. Anyone else is a 404, never a 403."""
        return members_visible_to(request, company)

    def visible_teams(self, request: Request, teams: QuerySet[Team]) -> QuerySet[Team]:
        """Which of the company's `teams` the team list shows."""
        membership = membership_of(request.user)  # type: ignore[arg-type]
        if membership is None:
            raise NotFound("You are not a member of a company.")
        if membership.role == CompanyRole.ADMIN:
            return teams
        if membership.team_id is None:
            return teams.none()
        return teams.filter(pk=membership.team_id)

    def counted_jobs(self, request: Request) -> QuerySet[Job]:
        """The jobs a team's `job_count` counts: the ones the user can see."""
        return jobs_visible_to(request.user)  # type: ignore[arg-type]

    def inviter(self, request: Request) -> str | None:
        """Who the invite email says sent it."""
        return getattr(request.user, "full_name", None) or None

    # Built on the hooks above.

    def teams_of(self, request: Request, company: Company) -> QuerySet[Team]:
        return team_queryset(company, self.counted_jobs(request))

    def member_or_404(
        self, request: Request, company: Company, member_id: UUID
    ) -> CompanyMembership:
        """404 for another team's member too, so nobody can probe outside their team."""
        membership = (
            self.visible_members(request, company)
            .filter(pk=member_id)
            .select_related("user", "company")
            .first()
        )
        if membership is None:
            raise NotFound("No such team member.")
        return membership

    def team_or_404(self, request: Request, company: Company, team_id: UUID) -> Team:
        team = self.teams_of(request, company).filter(pk=team_id).first()
        if team is None:
            raise NotFound("No such team.")
        return team


@extend_schema_view(
    get=extend_schema(
        operation_id="company_members_list",
        tags=["companies"],
        responses=MemberSerializer(many=True),
    ),
    post=extend_schema(
        operation_id="company_members_create",
        tags=["companies"],
        request=MemberInviteSerializer,
        responses={201: MemberSerializer},
    ),
)
class MemberListCreateView(TeamScope, ListCreateAPIView[CompanyMembership]):
    serializer_class = MemberSerializer
    pagination_class = None

    def get_queryset(self) -> QuerySet[CompanyMembership]:
        company = self.company_for(self.request, **self.kwargs)
        return self.visible_members(self.request, company).select_related("user", "team")

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        company = self.company_for(request, **self.kwargs)
        self.require_manage(request, company)
        payload = MemberInviteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        with transaction.atomic():
            team = None
            if data["role"] != CompanyRole.ADMIN:
                team = resolve_team(
                    company, team_id=data.get("team"), new_team_name=data.get("new_team_name")
                )
            membership = invite_member(
                company,
                email=data["email"],
                full_name=data["full_name"],
                role=data["role"],
                team=team,
                can_publish=data["can_publish"],
                locked_detail=self.locked_invite_detail,
            )
            send_invite(membership, invited_by=self.inviter(request))
        body = MemberSerializer(membership, context=self.get_serializer_context()).data
        return Response(body, status=201)


@extend_schema_view(
    patch=extend_schema(
        operation_id="company_member_partial_update",
        tags=["companies"],
        request=MemberUpdateSerializer,
        responses=MemberSerializer,
    ),
    delete=extend_schema(
        operation_id="company_member_destroy", tags=["companies"], responses={204: None}
    ),
)
class MemberDetailView(TeamScope, APIView):
    def patch(self, request: Request, id: UUID, **kwargs: Any) -> Response:
        company = self.company_for(request, **self.kwargs)
        membership = self.member_or_404(request, company, id)
        self.require_manage(request, company)
        payload = MemberUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        changes: dict[str, Any] = {}
        if "role" in data:
            changes["role"] = data["role"]
        if "can_publish" in data:
            changes["can_publish"] = data["can_publish"]
        target_role = data.get("role", membership.role)
        with transaction.atomic():
            if target_role != CompanyRole.ADMIN and ("team" in data or "new_team_name" in data):
                changes["team"] = resolve_team(
                    company,
                    team_id=data.get("team"),
                    new_team_name=data.get("new_team_name"),
                )
            update_member(membership, **changes)
        membership = self.member_or_404(request, company, id)
        return Response(MemberSerializer(membership, context={"request": request}).data)

    def delete(self, request: Request, id: UUID, **kwargs: Any) -> Response:
        company = self.company_for(request, **self.kwargs)
        membership = self.member_or_404(request, company, id)
        self.require_manage(request, company)
        remove_member(membership, actor=request.user)  # type: ignore[arg-type]
        return Response(status=204)


@extend_schema_view(
    post=extend_schema(
        operation_id="company_member_resend_invite_create",
        tags=["companies"],
        request=None,
        responses={204: None},
    )
)
class MemberResendInviteView(TeamScope, APIView):
    def post(self, request: Request, id: UUID, **kwargs: Any) -> Response:
        company = self.company_for(request, **self.kwargs)
        membership = self.member_or_404(request, company, id)
        self.require_manage(request, company)
        if membership.user.has_usable_password():
            raise MemberRuleError("This person has already set a password.")
        # Sparktal locked it; allauth would mail the switched-off address "no Sparktal account".
        if membership.user.locked_at is not None:
            raise MemberRuleError(self.locked_resend_detail)
        send_invite(membership, invited_by=self.inviter(request))
        return Response(status=204)


@extend_schema_view(
    get=extend_schema(
        operation_id="company_teams_list",
        tags=["companies"],
        responses=TeamSerializer(many=True),
    ),
    post=extend_schema(
        operation_id="company_teams_create",
        tags=["companies"],
        request=TeamWriteSerializer,
        responses={201: TeamSerializer},
    ),
)
class TeamListCreateView(TeamScope, ListCreateAPIView[Team]):
    serializer_class = TeamSerializer
    pagination_class = None

    def get_queryset(self) -> QuerySet[Team]:
        company = self.company_for(self.request, **self.kwargs)
        return self.visible_teams(self.request, self.teams_of(self.request, company))

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        company = self.company_for(request, **self.kwargs)
        self.require_manage(request, company)
        payload = TeamWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        team = create_team(company, payload.validated_data["name"])
        fresh = self.teams_of(request, company).get(pk=team.pk)
        return Response(TeamSerializer(fresh).data, status=201)


@extend_schema_view(
    patch=extend_schema(
        operation_id="company_team_partial_update",
        tags=["companies"],
        request=TeamWriteSerializer,
        responses=TeamSerializer,
    ),
    delete=extend_schema(
        operation_id="company_team_destroy", tags=["companies"], responses={204: None}
    ),
)
class TeamDetailView(TeamScope, APIView):
    def patch(self, request: Request, id: UUID, **kwargs: Any) -> Response:
        company = self.company_for(request, **self.kwargs)
        team = self.team_or_404(request, company, id)
        self.require_manage(request, company)
        payload = TeamWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        rename_team(team, payload.validated_data["name"])
        fresh = self.teams_of(request, company).get(pk=team.pk)
        return Response(TeamSerializer(fresh).data)

    def delete(self, request: Request, id: UUID, **kwargs: Any) -> Response:
        company = self.company_for(request, **self.kwargs)
        team = self.team_or_404(request, company, id)
        self.require_manage(request, company)
        delete_team(team)
        return Response(status=204)
