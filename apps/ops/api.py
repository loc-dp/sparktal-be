from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.generics import ListAPIView, ListCreateAPIView, RetrieveAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.companies.api import (
    MemberDetailView,
    MemberListCreateView,
    MemberResendInviteView,
    TeamDetailView,
    TeamListCreateView,
    TeamScope,
    send_invite,
)
from apps.companies.members import ACCOUNT_LOCKED, LOCKED_NO_INVITE, set_locked
from apps.companies.models import Company, CompanyMembership, Team
from apps.companies.provisioning import SPARKTAL_INVITER, provision_company
from apps.companies.serializers import (
    MemberInviteSerializer,
    MemberSerializer,
    MemberUpdateSerializer,
    TeamSerializer,
    TeamWriteSerializer,
)
from apps.core.pagination import DefaultPagination
from apps.hiring.api import Conflict
from apps.hiring.models import Placement
from apps.hiring.placements import PlacementConflict, mark_paid
from apps.jobs.distribution import set_bounty
from apps.jobs.models import Job
from apps.ops.filters import (
    OpsCompanyFilterSet,
    OpsJobFilterSet,
    OpsPlacementFilterSet,
    OpsRecruiterFilterSet,
)
from apps.ops.jobs import (
    distribute_with_bounty,
    ops_job_detail_queryset,
    ops_jobs,
    posted_jobs,
    update_channels,
)
from apps.ops.overview import ops_overview
from apps.ops.permissions import IsSparktalStaff
from apps.ops.queries import annotate_ops_companies, annotate_ops_recruiters, ops_placements
from apps.ops.serializers import (
    OpsBountySerializer,
    OpsChannelsSerializer,
    OpsCompanyCreateSerializer,
    OpsCompanySerializer,
    OpsDistributeSerializer,
    OpsJobDetailSerializer,
    OpsJobListItemSerializer,
    OpsMarkPaidSerializer,
    OpsOverviewSerializer,
    OpsPlacementSerializer,
    OpsRecruiterRejectSerializer,
    OpsRecruiterSerializer,
)
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.review import approve_recruiter, reject_recruiter

BOUNTY_FIELDS = ("bounty_type", "bounty_pct", "bounty_amount")
# What the bounty form sets: the bounty, and the guarantee days when it names them.
BOUNTY_FORM_FIELDS = (*BOUNTY_FIELDS, "guarantee_days")


def bounty_form(validated: dict[str, Any]) -> dict[str, Any]:
    return {name: validated[name] for name in BOUNTY_FORM_FIELDS if name in validated}


@extend_schema_view(
    get=extend_schema(
        tags=["ops"], operation_id="ops_overview_retrieve", responses=OpsOverviewSerializer
    )
)
class OpsOverviewView(APIView):
    permission_classes = (IsSparktalStaff,)

    def get(self, request: Request) -> Response:
        return Response(OpsOverviewSerializer(ops_overview()).data)


@extend_schema_view(get=extend_schema(tags=["ops"], operation_id="ops_jobs_list"))
class OpsJobListView(ListAPIView[Job]):
    permission_classes = (IsSparktalStaff,)
    serializer_class = OpsJobListItemSerializer
    pagination_class = DefaultPagination
    filter_backends = (DjangoFilterBackend,)
    filterset_class = OpsJobFilterSet

    def get_queryset(self) -> QuerySet[Job]:
        return ops_jobs()


@extend_schema_view(get=extend_schema(tags=["ops"], operation_id="ops_jobs_retrieve"))
class OpsJobDetailView(RetrieveAPIView[Job]):
    permission_classes = (IsSparktalStaff,)
    serializer_class = OpsJobDetailSerializer
    lookup_field = "id"

    def get_queryset(self) -> QuerySet[Job]:
        return ops_job_detail_queryset()


class OpsJobActionView(APIView):
    # A change to one job. Each answers with the job re-read as `OpsJobDetail`.
    permission_classes = (IsSparktalStaff,)

    def staff(self) -> User:
        user = self.request.user
        assert isinstance(user, User), "IsSparktalStaff must run before this view"
        return user

    def job(self, id: UUID) -> Job:
        """The job ops can see, or a 404."""
        return get_object_or_404(ops_jobs(), pk=id)

    def detail(self, id: UUID) -> Response:
        # Never the instance the change was made on: it has no counts, invites or office read.
        return Response(
            OpsJobDetailSerializer(get_object_or_404(ops_job_detail_queryset(), pk=id)).data
        )


@extend_schema_view(
    put=extend_schema(
        tags=["ops"],
        operation_id="ops_jobs_bounty_update",
        description="Set the bounty of a posted job. It applies at once to every recruiter on it.",
        request=OpsBountySerializer,
        responses=OpsJobDetailSerializer,
    )
)
class OpsJobBountyView(OpsJobActionView):
    def put(self, request: Request, id: UUID) -> Response:
        job = self.job(id)
        bounty = OpsBountySerializer(data=request.data)
        bounty.is_valid(raise_exception=True)
        set_bounty(job, **bounty_form(bounty.validated_data))
        return self.detail(id)


@extend_schema_view(
    post=extend_schema(
        tags=["ops"],
        operation_id="ops_jobs_distribute_create",
        description=(
            "Set the bounty and take a job out of the queue, on the marketplace, to the "
            "recruiters picked, or both. Nothing is saved unless the whole distribution works."
        ),
        request=OpsDistributeSerializer,
        responses=OpsJobDetailSerializer,
    )
)
class OpsJobDistributeView(OpsJobActionView):
    def post(self, request: Request, id: UUID) -> Response:
        # Any job, so a page gone stale on a deleted or closed one reads "no longer waiting".
        get_object_or_404(Job, pk=id)
        form = OpsDistributeSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        distribute_with_bounty(
            id,
            actor=self.staff(),
            bounty=bounty_form(form.validated_data),
            on_marketplace=form.validated_data["on_marketplace"],
            recruiters=form.validated_data["recruiters"],
        )
        return self.detail(id)


@extend_schema_view(
    patch=extend_schema(
        tags=["ops"],
        operation_id="ops_jobs_channels_partial_update",
        description=(
            "Switch the marketplace, withdraw pending invites and invite more recruiters on a "
            "distributed job. Nothing is saved unless every change works."
        ),
        request=OpsChannelsSerializer,
        responses=OpsJobDetailSerializer,
    )
)
class OpsJobChannelsView(OpsJobActionView):
    def patch(self, request: Request, id: UUID) -> Response:
        job = self.job(id)
        # Partial, so a field that is absent means "leave it": a form-encoded body has no way to
        # say "no value", and DRF reads an absent boolean there as False, which would switch the
        # marketplace off whenever only invites are sent.
        form = OpsChannelsSerializer(data=request.data, partial=True)
        form.is_valid(raise_exception=True)
        update_channels(
            job,
            actor=self.staff(),
            on_marketplace=form.validated_data.get("on_marketplace"),
            cancel_invite_ids=form.validated_data.get("cancel_invite_ids", []),
            invite=form.validated_data["invite_recruiters"],
        )
        return self.detail(id)


# Companies -----------------------------------------------------------------------------------


def ops_companies() -> QuerySet[Company]:
    return annotate_ops_companies(Company.objects.all()).order_by("name", "id")


@extend_schema_view(
    get=extend_schema(tags=["ops"], operation_id="ops_companies_list"),
    post=extend_schema(
        tags=["ops"],
        operation_id="ops_companies_create",
        description=(
            "Open a company account and email its first admin an invite. Nothing is saved "
            "unless the invite goes out."
        ),
        request=OpsCompanyCreateSerializer,
        responses={201: OpsCompanySerializer},
    ),
)
class OpsCompanyListCreateView(ListCreateAPIView[Company]):
    permission_classes = (IsSparktalStaff,)
    serializer_class = OpsCompanySerializer
    pagination_class = DefaultPagination
    filter_backends = (DjangoFilterBackend,)
    filterset_class = OpsCompanyFilterSet

    def get_queryset(self) -> QuerySet[Company]:
        return ops_companies()

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        form = OpsCompanyCreateSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        with transaction.atomic():
            admin = provision_company(**form.validated_data)
            send_invite(admin, invited_by=SPARKTAL_INVITER)
        company = get_object_or_404(ops_companies(), pk=admin.company_id)
        return Response(OpsCompanySerializer(company).data, status=201)


@extend_schema_view(get=extend_schema(tags=["ops"], operation_id="ops_companies_retrieve"))
class OpsCompanyDetailView(RetrieveAPIView[Company]):
    permission_classes = (IsSparktalStaff,)
    serializer_class = OpsCompanySerializer
    lookup_url_kwarg = "company_id"

    def get_queryset(self) -> QuerySet[Company]:
        return ops_companies()


# A company's members and teams ---------------------------------------------------------------


class OpsCompanyScope(TeamScope, APIView):
    # The company team views (`/company/members`, `/company/teams`), for Sparktal staff.
    #
    # Each ops view subclasses the company one with this first, so every hook of `TeamScope` is
    # answered here: the company is the one in the URL, staff see all of its members and teams
    # (there is no seat of theirs to scope by), the team job counts are the posted jobs ops can
    # see, and invites are signed by Sparktal. `IsSparktalStaff` has already decided who may act.
    # Comments, not a docstring, so the schema does not show this as each view's description.

    permission_classes = (IsSparktalStaff,)
    # Sparktal locked the account and is the one who can unlock it, so it is told plainly.
    locked_invite_detail = ACCOUNT_LOCKED
    locked_resend_detail = LOCKED_NO_INVITE

    def company_for(self, request: Request, **kwargs: Any) -> Company:
        return get_object_or_404(Company, pk=kwargs["company_id"])

    def require_manage(self, request: Request, company: Company) -> None:
        """Nothing to check: the permission class let only Sparktal staff in."""

    def visible_members(self, request: Request, company: Company) -> QuerySet[CompanyMembership]:
        return CompanyMembership.objects.filter(company=company)

    def visible_teams(self, request: Request, teams: QuerySet[Team]) -> QuerySet[Team]:
        return teams

    def counted_jobs(self, request: Request) -> QuerySet[Job]:
        return posted_jobs()

    def inviter(self, request: Request) -> str | None:
        return SPARKTAL_INVITER


@extend_schema_view(
    get=extend_schema(
        tags=["ops"],
        operation_id="ops_company_members_list",
        responses=MemberSerializer(many=True),
    ),
    post=extend_schema(
        tags=["ops"],
        operation_id="ops_company_members_create",
        description="Invite a member, an admin included. Nothing is saved unless the email goes.",
        request=MemberInviteSerializer,
        responses={201: MemberSerializer},
    ),
)
class OpsMemberListCreateView(OpsCompanyScope, MemberListCreateView):
    pass


@extend_schema_view(
    patch=extend_schema(
        tags=["ops"],
        operation_id="ops_company_members_partial_update",
        request=MemberUpdateSerializer,
        responses=MemberSerializer,
    ),
    delete=extend_schema(
        tags=["ops"], operation_id="ops_company_members_destroy", responses={204: None}
    ),
)
class OpsMemberDetailView(OpsCompanyScope, MemberDetailView):
    pass


@extend_schema_view(
    post=extend_schema(
        tags=["ops"],
        operation_id="ops_company_members_resend_invite_create",
        request=None,
        responses={204: None},
    )
)
class OpsMemberResendInviteView(OpsCompanyScope, MemberResendInviteView):
    pass


class OpsMemberLockingView(OpsCompanyScope):
    locked: bool

    def post(self, request: Request, id: UUID, **kwargs: Any) -> Response:
        company = self.company_for(request, **self.kwargs)
        membership = self.member_or_404(request, company, id)
        set_locked(membership, locked=self.locked)
        return Response(MemberSerializer(membership, context={"request": request}).data)


@extend_schema_view(
    post=extend_schema(
        tags=["ops"],
        operation_id="ops_company_members_lock_create",
        description=(
            "Switch the member's account off. They are signed out on their next request and "
            "cannot sign in; their seat, team and jobs stay."
        ),
        request=None,
        responses=MemberSerializer,
    )
)
class OpsMemberLockView(OpsMemberLockingView):
    locked = True


@extend_schema_view(
    post=extend_schema(
        tags=["ops"],
        operation_id="ops_company_members_unlock_create",
        description="Switch the member's account back on. They sign in again as before.",
        request=None,
        responses=MemberSerializer,
    )
)
class OpsMemberUnlockView(OpsMemberLockingView):
    locked = False


@extend_schema_view(
    get=extend_schema(
        tags=["ops"],
        operation_id="ops_company_teams_list",
        responses=TeamSerializer(many=True),
    ),
    post=extend_schema(
        tags=["ops"],
        operation_id="ops_company_teams_create",
        request=TeamWriteSerializer,
        responses={201: TeamSerializer},
    ),
)
class OpsTeamListCreateView(OpsCompanyScope, TeamListCreateView):
    pass


@extend_schema_view(
    patch=extend_schema(
        tags=["ops"],
        operation_id="ops_company_teams_partial_update",
        request=TeamWriteSerializer,
        responses=TeamSerializer,
    ),
    delete=extend_schema(
        tags=["ops"], operation_id="ops_company_teams_destroy", responses={204: None}
    ),
)
class OpsTeamDetailView(OpsCompanyScope, TeamDetailView):
    pass


# Recruiter review ----------------------------------------------------------------------------


def ops_recruiters() -> QuerySet[RecruiterProfile]:
    return annotate_ops_recruiters(RecruiterProfile.objects.all())


@extend_schema_view(
    get=extend_schema(
        tags=["ops"],
        operation_id="ops_recruiters_list",
        description=(
            "Every recruiter, or the ones in one state. The review queue is `pending`, oldest "
            "submission first; the rest read by nickname."
        ),
    )
)
class OpsRecruiterListView(ListAPIView[RecruiterProfile]):
    permission_classes = (IsSparktalStaff,)
    serializer_class = OpsRecruiterSerializer
    pagination_class = DefaultPagination
    filter_backends = (DjangoFilterBackend,)
    filterset_class = OpsRecruiterFilterSet

    def get_queryset(self) -> QuerySet[RecruiterProfile]:
        return ops_recruiters()


class OpsRecruiterReviewView(APIView):
    # A decision on one recruiter. Each answers with the recruiter re-read as `OpsRecruiter`.
    permission_classes = (IsSparktalStaff,)

    def staff(self) -> User:
        user = self.request.user
        assert isinstance(user, User), "IsSparktalStaff must run before this view"
        return user

    def recruiter(self, id: UUID) -> RecruiterProfile:
        """The recruiter, or a 404."""
        return get_object_or_404(RecruiterProfile, pk=id)

    def row(self, id: UUID) -> Response:
        # Never the instance the decision was made on: it has no `active_jobs`.
        return Response(OpsRecruiterSerializer(get_object_or_404(ops_recruiters(), pk=id)).data)


@extend_schema_view(
    post=extend_schema(
        tags=["ops"],
        operation_id="ops_recruiters_approve_create",
        description=(
            "Let a recruiter in and email them. It is a 400 if they are already approved; a "
            "rejected recruiter can be approved."
        ),
        request=None,
        responses=OpsRecruiterSerializer,
    )
)
class OpsRecruiterApproveView(OpsRecruiterReviewView):
    def post(self, request: Request, id: UUID) -> Response:
        approve_recruiter(self.recruiter(id), by=self.staff())
        return self.row(id)


@extend_schema_view(
    post=extend_schema(
        tags=["ops"],
        operation_id="ops_recruiters_reject_create",
        description=(
            "Turn a recruiter down and email them the note. The note is required, and it is a "
            "400 if they are already rejected; an approved recruiter can be rejected."
        ),
        request=OpsRecruiterRejectSerializer,
        responses=OpsRecruiterSerializer,
    )
)
class OpsRecruiterRejectView(OpsRecruiterReviewView):
    def post(self, request: Request, id: UUID) -> Response:
        recruiter = self.recruiter(id)
        form = OpsRecruiterRejectSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        reject_recruiter(recruiter, by=self.staff(), note=form.validated_data["note"])
        return self.row(id)


@extend_schema_view(get=extend_schema(tags=["ops"], operation_id="ops_placements_list"))
class OpsPlacementListView(ListAPIView[Placement]):
    """Every hire on the platform, newest start date first: the payouts Sparktal owes and made."""

    permission_classes = (IsSparktalStaff,)
    serializer_class = OpsPlacementSerializer
    pagination_class = DefaultPagination
    filter_backends = (DjangoFilterBackend,)
    filterset_class = OpsPlacementFilterSet

    def get_queryset(self) -> QuerySet[Placement]:
        return ops_placements().order_by("-start_date", "-id")


@extend_schema_view(
    post=extend_schema(
        tags=["ops"],
        operation_id="ops_placements_mark_paid_create",
        description="Record that Sparktal paid the recruiter. Only a due placement can be paid.",
        request=OpsMarkPaidSerializer,
        responses=OpsPlacementSerializer,
    )
)
class OpsPlacementMarkPaidView(APIView):
    permission_classes = (IsSparktalStaff,)

    def post(self, request: Request, pk: UUID) -> Response:
        staff = request.user
        assert isinstance(staff, User), "IsSparktalStaff must run before this view"
        placement = get_object_or_404(ops_placements(), pk=pk)
        form = OpsMarkPaidSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            mark_paid(
                placement,
                staff,
                form.validated_data["paid_amount"],
                form.validated_data["paid_at"],
                form.validated_data["note"],
                timezone.localdate(),
            )
        except PlacementConflict as error:
            raise Conflict(str(error)) from error
        return Response(OpsPlacementSerializer(get_object_or_404(ops_placements(), pk=pk)).data)
