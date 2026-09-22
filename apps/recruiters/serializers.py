import re
from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models.functions import Lower
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import User, UserKind
from apps.jobs.models import Job, JobFunction
from apps.recruiters.models import RecruiterProfile

NICKNAME_RE = re.compile(r"^(?!.*_)[\w &.\-]{3,60}$", re.UNICODE)
NICKNAME_MESSAGE = "Use 3 to 60 characters: letters, numbers, spaces, & . -"
NICKNAME_TAKEN = "That nickname is taken."
EMAIL_TAKEN = "An account with this email already exists."
TERMS_MESSAGE = "Accept the marketplace rules to continue."
MAX_EXPERTISE = 5


def nickname_is_free(value: str, *, exclude_pk: Any = None) -> bool:
    taken = RecruiterProfile.objects.annotate(lowered=Lower("nickname")).filter(
        lowered=value.strip().lower()
    )
    if exclude_pk is not None:
        taken = taken.exclude(pk=exclude_pk)
    return not taken.exists()


def clean_nickname(value: str) -> str:
    candidate = " ".join(value.split())
    if not NICKNAME_RE.fullmatch(candidate):
        raise serializers.ValidationError(NICKNAME_MESSAGE)
    return candidate


def clean_expertise(value: list[str]) -> list[str]:
    allowed = {choice.value for choice in JobFunction}
    if len(value) > MAX_EXPERTISE:
        raise serializers.ValidationError(f"Pick at most {MAX_EXPERTISE} areas.")
    if any(item not in allowed for item in value):
        raise serializers.ValidationError("Pick areas from the list.")
    return value


class SignupSerializer(serializers.Serializer[RecruiterProfile]):
    email = serializers.EmailField()
    full_name = serializers.CharField(max_length=120)
    nickname = serializers.CharField(max_length=60)
    password = serializers.CharField(write_only=True)
    agreed_terms = serializers.BooleanField()

    def validate_email(self, value: str) -> str:
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(EMAIL_TAKEN)
        return value.lower()

    def validate_nickname(self, value: str) -> str:
        candidate = clean_nickname(value)
        if not nickname_is_free(candidate):
            raise serializers.ValidationError(NICKNAME_TAKEN)
        return candidate

    def validate_password(self, value: str) -> str:
        try:
            validate_password(value)
        except DjangoValidationError as error:
            raise serializers.ValidationError(list(error.messages)) from error
        return value

    def validate_agreed_terms(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError(TERMS_MESSAGE)
        return value

    @transaction.atomic
    def create(self, validated_data: dict[str, Any]) -> RecruiterProfile:
        now = timezone.now()
        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            full_name=validated_data["full_name"],
            kind=UserKind.RECRUITER,
        )
        return RecruiterProfile.objects.create(
            user=user,
            nickname=validated_data["nickname"],
            agreed_terms_at=now,
            submitted_at=now,
        )


class RecruiterProfileSerializer(serializers.ModelSerializer[RecruiterProfile]):
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = RecruiterProfile
        fields = (
            "id",
            "email",
            "full_name",
            "nickname",
            "status",
            "phone",
            "expertise",
            "years_experience",
            "linkedin",
            "bio",
            "review_note",
            "submitted_at",
            "reviewed_at",
        )
        read_only_fields = ("id", "status", "review_note", "submitted_at", "reviewed_at")

    def validate_nickname(self, value: str) -> str:
        candidate = clean_nickname(value)
        if not nickname_is_free(candidate, exclude_pk=self.instance.pk if self.instance else None):
            raise serializers.ValidationError(NICKNAME_TAKEN)
        return candidate

    def validate_expertise(self, value: list[str]) -> list[str]:
        return clean_expertise(value)

    def validate_bio(self, value: str) -> str:
        if len(value) > 600:
            raise serializers.ValidationError("Keep your bio under 600 characters.")
        return value


class SignupResultSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    nickname = serializers.CharField()
    status = serializers.CharField()


class NicknameCheckSerializer(serializers.Serializer[Any]):
    available = serializers.BooleanField()


class RecruiterJobSerializer(serializers.ModelSerializer[Job]):
    company = serializers.SerializerMethodField()
    location_label = serializers.CharField(read_only=True)
    submitted_count = serializers.IntegerField(read_only=True)
    active_count = serializers.IntegerField(read_only=True)
    joined_at = serializers.DateTimeField(read_only=True)
    last_activity_at = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = Job
        fields = (
            "id",
            "title",
            "function",
            "status",
            "company",
            "work_model",
            "location_label",
            "salary_min",
            "salary_max",
            "bounty_type",
            "bounty_pct",
            "bounty_amount",
            "joined_at",
            "submitted_count",
            "active_count",
            "last_activity_at",
        )

    @extend_schema_field(serializers.DictField(child=serializers.CharField()))
    def get_company(self, job: Job) -> dict[str, str]:
        return {"id": str(job.company_id), "name": job.company.name}


class WaitingJobSerializer(serializers.Serializer[dict[str, Any]]):
    id = serializers.UUIDField()
    title = serializers.CharField()
    company_name = serializers.CharField()
    location_label = serializers.CharField()
    work_model = serializers.CharField()
    salary_min = serializers.DecimalField(max_digits=15, decimal_places=0, allow_null=True)
    salary_max = serializers.DecimalField(max_digits=15, decimal_places=0, allow_null=True)
    bounty_type = serializers.CharField()
    bounty_pct = serializers.IntegerField(allow_null=True)
    bounty_amount = serializers.DecimalField(max_digits=15, decimal_places=0, allow_null=True)
    published_at = serializers.DateTimeField(allow_null=True)


class RecruiterOverviewSerializer(serializers.Serializer[dict[str, Any]]):
    current_jobs = serializers.IntegerField()
    jobs_waiting_for_cv = serializers.IntegerField()
    cv_sent = serializers.IntegerField()
    cv_screened = serializers.IntegerField()
    cv_in_interviews = serializers.IntegerField()
    offer = serializers.IntegerField()
    marketplace_open_count = serializers.IntegerField()
    waiting_jobs = WaitingJobSerializer(many=True)
