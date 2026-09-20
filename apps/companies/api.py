# UploadedFile is generic for django-stubs but not subscriptable at runtime, so
# annotations stay lazy in this module.
from __future__ import annotations

from typing import Any

from django.core.files.uploadedfile import UploadedFile
from drf_spectacular.utils import extend_schema, extend_schema_view
from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.companies.access import company_of
from apps.companies.models import Company
from apps.companies.serializers import (
    CompanyCompleteSerializer,
    CompanySerializer,
    ImageUploadSerializer,
)

# Declared inline rather than as a component: a $ref'd binary schema comes out of
# the generator as `unknown` instead of a Blob, which breaks the typed client.
UPLOAD_REQUEST = {
    "multipart/form-data": {
        "type": "object",
        "properties": {"file": {"type": "string", "format": "binary"}},
        "required": ["file"],
    }
}

# SVG is executable in a browser, so only raster formats are accepted and every
# upload is verified by Pillow rather than trusted by extension.
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
    # Multipart only: a urlencoded form cannot carry a file, and advertising it
    # makes the generated client type the field as `unknown` instead of a Blob.
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
