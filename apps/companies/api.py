from __future__ import annotations

from typing import Any
from uuid import UUID

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema, extend_schema_view
from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.invites import InviteError, send_password_invite
from apps.companies.access import company_of
from apps.companies.home import home_payload
from apps.companies.members import MemberRuleError, change_role, invite_member, remove_member
from apps.companies.models import Company, CompanyMembership
from apps.companies.serializers import (
    CompanyCompleteSerializer,
    CompanyHomeSerializer,
    CompanySerializer,
    ImageUploadSerializer,
    MemberInviteSerializer,
    MemberRoleSerializer,
    MemberSerializer,
)

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


def require_manage_members(request: Request, company: Company) -> None:
    if not request.user.has_perm("companies.manage_members", company):
        raise PermissionDenied("Only a Company Admin can manage the team.")


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
class MemberListCreateView(ListCreateAPIView[CompanyMembership]):
    serializer_class = MemberSerializer
    pagination_class = None

    def get_queryset(self) -> QuerySet[CompanyMembership]:
        company = get_company_or_404(self.request)
        return CompanyMembership.objects.filter(company=company).select_related("user")

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        company = get_company_or_404(request)
        require_manage_members(request, company)
        payload = MemberInviteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        with transaction.atomic():
            membership = invite_member(company, **payload.validated_data)
            try:
                send_password_invite(membership.user.email)
            except InviteError as exc:
                raise InviteEmailFailed() from exc
        data = MemberSerializer(membership, context=self.get_serializer_context()).data
        return Response(data, status=201)


def member_or_404(request: Request, member_id: UUID) -> CompanyMembership:
    company = get_company_or_404(request)
    membership = (
        CompanyMembership.objects.filter(company=company, pk=member_id)
        .select_related("user", "company")
        .first()
    )
    if membership is None:
        raise NotFound("No such team member.")
    return membership


@extend_schema_view(
    patch=extend_schema(
        operation_id="company_member_partial_update",
        tags=["companies"],
        request=MemberRoleSerializer,
        responses=MemberSerializer,
    ),
    delete=extend_schema(
        operation_id="company_member_destroy", tags=["companies"], responses={204: None}
    ),
)
class MemberDetailView(APIView):
    def patch(self, request: Request, id: UUID) -> Response:
        membership = member_or_404(request, id)
        require_manage_members(request, membership.company)
        payload = MemberRoleSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        change_role(membership, payload.validated_data["role"])
        return Response(MemberSerializer(membership, context={"request": request}).data)

    def delete(self, request: Request, id: UUID) -> Response:
        membership = member_or_404(request, id)
        require_manage_members(request, membership.company)
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
class MemberResendInviteView(APIView):
    def post(self, request: Request, id: UUID) -> Response:
        membership = member_or_404(request, id)
        require_manage_members(request, membership.company)
        if membership.user.has_usable_password():
            raise MemberRuleError("This person has already set a password.")
        try:
            send_password_invite(membership.user.email)
        except InviteError as exc:
            raise InviteEmailFailed() from exc
        return Response(status=204)
