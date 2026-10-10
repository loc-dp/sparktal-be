from typing import Any
from urllib.parse import urlparse

from django.db.models.fields.files import FieldFile
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.companies.models import Company, CompanyMembership, CompanyRole, Location, Office

ABOUT_MIN = 100
ABOUT_MAX = 2000
MAX_OFFICES = 10


def _is_http_url(url: str, suffixes: tuple[str, ...] | None = None) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if suffixes is None:
        return True
    host = parsed.hostname.lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)


class OfficeSerializer(serializers.ModelSerializer[Office]):
    address = serializers.CharField(max_length=255, allow_blank=True)

    class Meta:
        model = Office
        fields = ("id", "address", "city", "position")
        read_only_fields = ("id", "position")

    def validate_address(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Enter this address or remove the row.")
        return value.strip()


class CompanySerializer(serializers.ModelSerializer[Company]):
    offices = OfficeSerializer(many=True, required=False)
    website = serializers.CharField(required=False, allow_blank=True, max_length=200)
    linkedin = serializers.CharField(required=False, allow_blank=True, max_length=200)
    facebook = serializers.CharField(required=False, allow_blank=True, max_length=200)
    logo_url = serializers.SerializerMethodField()
    banner_url = serializers.SerializerMethodField()
    profile_completed = serializers.BooleanField(read_only=True)
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = Company
        fields = (
            "id",
            "name",
            "industry",
            "company_type",
            "year_incorporated",
            "size_range",
            "bd_headcount",
            "located",
            "about",
            "website",
            "linkedin",
            "facebook",
            "logo_url",
            "banner_url",
            "profile_completed",
            "offices",
            "can_edit",
        )
        read_only_fields = ("id", "logo_url", "banner_url", "profile_completed", "can_edit")

    def _absolute(self, file_field: FieldFile) -> str | None:
        if not file_field:
            return None
        request = self.context.get("request")
        url: str = file_field.url
        return request.build_absolute_uri(url) if request else url

    def get_logo_url(self, company: Company) -> str | None:
        return self._absolute(company.logo)

    def get_banner_url(self, company: Company) -> str | None:
        return self._absolute(company.banner)

    def get_can_edit(self, company: Company) -> bool:
        request = self.context.get("request")
        return bool(request and request.user.has_perm("companies.change_company", company))

    def validate_name(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Enter your company name.")
        return value.strip()

    def validate_year_incorporated(self, value: int | None) -> int | None:
        if value is None:
            return value
        current_year = timezone.localdate().year
        if value < 1900 or value > current_year:
            raise serializers.ValidationError(f"Enter a year between 1900 and {current_year}.")
        return value

    def validate_website(self, value: str) -> str:
        if value and not _is_http_url(value):
            raise serializers.ValidationError("Enter a full link, e.g. https://velapay.vn")
        return value

    def validate_linkedin(self, value: str) -> str:
        if value and not _is_http_url(value, ("linkedin.com",)):
            raise serializers.ValidationError(
                "Enter a LinkedIn page link, e.g. https://www.linkedin.com/company/velapay"
            )
        return value

    def validate_facebook(self, value: str) -> str:
        if value and not _is_http_url(value, ("facebook.com", "fb.com")):
            raise serializers.ValidationError(
                "Enter a Facebook page link, e.g. https://www.facebook.com/velapay.vn"
            )
        return value

    def validate_about(self, value: str) -> str:
        if len(value) > ABOUT_MAX:
            raise serializers.ValidationError(f"Keep this under {ABOUT_MAX} characters.")
        return value

    def validate_offices(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > MAX_OFFICES:
            raise serializers.ValidationError(f"Up to {MAX_OFFICES} offices.")
        return value

    def update(self, instance: Company, validated_data: dict[str, Any]) -> Company:
        offices = validated_data.pop("offices", None)
        company = super().update(instance, validated_data)
        if offices is not None:
            company.offices.all().delete()
            Office.objects.bulk_create(
                Office(
                    company=company,
                    address=office["address"],
                    city=office.get("city", Location.HCM),
                    position=index,
                )
                for index, office in enumerate(offices)
            )
        return company


class CompanyCompleteSerializer(serializers.Serializer[Company]):
    """Validates that everything marked * in the UI is filled before publishing."""

    REQUIRED_MESSAGES = {  # noqa: RUF012
        "industry": "Select an industry.",
        "name": "Enter your company name.",
        "company_type": "Choose FDI or Vietnamese company.",
        "year_incorporated": "Enter the year your company was incorporated.",
        "size_range": "Select your company size in Vietnam.",
        "bd_headcount": "Enter the number of BD staff in Vietnam (0 if none).",
        "located": "Select where your company is located in Vietnam.",
    }

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        company: Company = self.context["company"]
        errors: dict[str, list[str]] = {}
        for field, message in self.REQUIRED_MESSAGES.items():
            value = getattr(company, field)
            if value in (None, ""):
                errors[field] = [message]

        about = company.about.strip()
        if not about:
            errors["about"] = ["Tell recruiters about your company."]
        elif len(about) < ABOUT_MIN:
            errors["about"] = [
                f"Write at least {ABOUT_MIN} characters so recruiters can pitch your company "
                f"({ABOUT_MIN - len(about)} to go)."
            ]

        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class ImageUploadSerializer(serializers.Serializer[Any]):
    file = serializers.FileField()


class HomeJobRowSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField()
    title = serializers.CharField()
    current_stage = serializers.CharField(allow_null=True)
    days_open = serializers.IntegerField()
    cv_count = serializers.IntegerField()
    new_count = serializers.IntegerField()


class HomeActionSerializer(serializers.Serializer[dict[str, Any]]):
    kind = serializers.CharField()
    title = serializers.CharField()
    detail = serializers.CharField()
    href = serializers.CharField()


class HomeJobCountsSerializer(serializers.Serializer[dict[str, int]]):
    pending = serializers.IntegerField()
    active = serializers.IntegerField()
    closed = serializers.IntegerField()


class HomePipelineSerializer(serializers.Serializer[dict[str, int]]):
    shortlisted = serializers.IntegerField()
    new = serializers.IntegerField()
    screen = serializers.IntegerField()
    interview = serializers.IntegerField()
    offer = serializers.IntegerField()
    onboarded = serializers.IntegerField()
    rejected = serializers.IntegerField()


class CompanyHomeSerializer(serializers.Serializer[dict[str, Any]]):
    jobs = HomeJobCountsSerializer()
    pipeline = HomePipelineSerializer()
    active_jobs = HomeJobRowSerializer(many=True)
    actions = HomeActionSerializer(many=True)


class MemberSerializer(serializers.ModelSerializer[CompanyMembership]):
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    status = serializers.SerializerMethodField()
    is_me = serializers.SerializerMethodField()

    class Meta:
        model = CompanyMembership
        fields = ("id", "full_name", "email", "role", "status", "is_me", "created_at")
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=["active", "invited"]))
    def get_status(self, obj: CompanyMembership) -> str:
        return "active" if obj.user.has_usable_password() else "invited"

    def get_is_me(self, obj: CompanyMembership) -> bool:
        request = self.context.get("request")
        return bool(request and obj.user_id == request.user.pk)


class MemberInviteSerializer(serializers.Serializer[dict[str, str]]):
    email = serializers.EmailField(max_length=254)
    full_name = serializers.CharField(max_length=120)
    role = serializers.ChoiceField(choices=CompanyRole.choices)


class MemberRoleSerializer(serializers.Serializer[dict[str, str]]):
    role = serializers.ChoiceField(choices=CompanyRole.choices)
