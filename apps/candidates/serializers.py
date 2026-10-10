from __future__ import annotations

from datetime import date
from typing import Any, ClassVar

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import UploadedFile
from rest_framework import serializers

from apps.candidates.cv_fields import MAX_AGE, MIN_AGE, is_working_age
from apps.candidates.cv_files import validate_cv
from apps.candidates.models import Application, Candidate, CvParse
from apps.candidates.parsing import MAX_YEARS


class ApplicationCandidateSerializer(serializers.Serializer[object]):
    full_name = serializers.CharField(read_only=True)
    current_title = serializers.CharField(read_only=True)


class ApplicationRecruiterSerializer(serializers.Serializer[object]):
    """Company users only see the recruiter's marketplace nickname."""

    id = serializers.UUIDField(read_only=True)
    nickname = serializers.CharField(read_only=True)


class ApplicationListSerializer(serializers.ModelSerializer[Application]):
    candidate = ApplicationCandidateSerializer(read_only=True)
    recruiter = ApplicationRecruiterSerializer(read_only=True)

    class Meta:
        model = Application
        fields = (
            "id",
            "candidate",
            "recruiter",
            "stage",
            "submitted_at",
            "stage_changed_at",
            "is_duplicate",
            "cv_filename",
        )
        read_only_fields = fields


class CvParseSerializer(serializers.ModelSerializer[CvParse]):
    class Meta:
        model = CvParse
        fields = ("id", "status", "fields", "error", "filename", "created_at", "finished_at")
        read_only_fields = fields


class CvParseCreateSerializer(serializers.Serializer[CvParse]):
    file = serializers.FileField(write_only=True)

    def validate_file(self, upload: UploadedFile[Any]) -> UploadedFile[Any]:
        try:
            validate_cv(upload)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from error
        return upload

    def create(self, validated_data: dict[str, Any]) -> CvParse:
        upload = validated_data["file"]
        return CvParse.objects.create(
            owner=self.context["recruiter"], file=upload, filename=upload.name or "cv.pdf"
        )


MAX_POOL_SKILLS = 20
MAX_LANGUAGES = 10
SALARY_NOT_NEGATIVE = "Salary can't be negative."


class CandidateSerializer(serializers.ModelSerializer[Candidate]):
    has_cv = serializers.SerializerMethodField()
    submitted_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Candidate
        fields = (
            "id",
            "full_name",
            "current_title",
            "email",
            "phone",
            "dob",
            "location",
            "is_expat",
            "languages",
            "skills",
            "years_experience",
            "current_salary",
            "expected_salary",
            "cv_filename",
            "has_cv",
            "notes",
            "submitted_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_has_cv(self, candidate: Candidate) -> bool:
        return bool(candidate.cv_file)


class CandidateWriteSerializer(serializers.ModelSerializer[Candidate]):
    class Meta:
        model = Candidate
        fields = (
            "full_name",
            "current_title",
            "email",
            "phone",
            "dob",
            "location",
            "is_expat",
            "languages",
            "skills",
            "years_experience",
            "current_salary",
            "expected_salary",
            "notes",
        )
        extra_kwargs: ClassVar[dict[str, Any]] = {
            "full_name": {
                "error_messages": {
                    "blank": "Enter the full name",
                    "required": "Enter the full name",
                }
            },
            "years_experience": {
                "max_value": MAX_YEARS,
                "error_messages": {"max_value": f"Up to {MAX_YEARS} years."},
            },
            "current_salary": {
                "min_value": 0,
                "error_messages": {"min_value": SALARY_NOT_NEGATIVE},
            },
            "expected_salary": {
                "min_value": 0,
                "error_messages": {"min_value": SALARY_NOT_NEGATIVE},
            },
        }

    def validate_dob(self, value: date | None) -> date | None:
        if value is not None and not is_working_age(value):
            raise serializers.ValidationError(
                f"Enter a date of birth for someone aged {MIN_AGE} to {MAX_AGE}."
            )
        return value

    def validate_skills(self, value: list[str]) -> list[str]:
        if len(value) > MAX_POOL_SKILLS:
            raise serializers.ValidationError(f"Up to {MAX_POOL_SKILLS} skills.")
        return [skill.strip() for skill in value if skill.strip()]

    def validate_languages(self, value: list[str]) -> list[str]:
        if len(value) > MAX_LANGUAGES:
            raise serializers.ValidationError(f"Up to {MAX_LANGUAGES} languages.")
        return [language.strip() for language in value if language.strip()]

    def create(self, validated_data: dict[str, Any]) -> Candidate:
        return Candidate.objects.create(owner=self.context["recruiter"], **validated_data)


class CandidateCvSerializer(serializers.Serializer[Candidate]):
    file = serializers.FileField(write_only=True)

    def validate_file(self, upload: UploadedFile[Any]) -> UploadedFile[Any]:
        try:
            validate_cv(upload)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from error
        return upload


class CandidateMatchSerializer(serializers.Serializer[Any]):
    """Who a new profile would be: any one of these can tie it to someone already in the pool."""

    email = serializers.CharField(max_length=254, required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    full_name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    dob = serializers.DateField(required=False, allow_null=True, default=None)


class DuplicateCheckSerializer(serializers.Serializer[Any]):
    candidate_id = serializers.UUIDField()


class DuplicateCheckResultSerializer(serializers.Serializer[Any]):
    duplicate = serializers.BooleanField()
    first_submitted_at = serializers.DateTimeField(allow_null=True)
    window_months = serializers.IntegerField()


class SubmissionCreateSerializer(serializers.Serializer[Any]):
    candidate_id = serializers.UUIDField()
    reason_for_leaving = serializers.CharField(max_length=4000, trim_whitespace=True)
    motivation = serializers.CharField(max_length=4000, trim_whitespace=True)
    other_notes = serializers.CharField(
        max_length=4000, required=False, allow_blank=True, default=""
    )
    consultant_note = serializers.CharField(
        max_length=4000, required=False, allow_blank=True, default=""
    )
    cv_file = serializers.FileField(required=False)

    def validate_cv_file(self, upload: UploadedFile[Any]) -> UploadedFile[Any]:
        try:
            validate_cv(upload)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from error
        return upload


class SubmissionResultSerializer(serializers.Serializer[Any]):
    application_id = serializers.UUIDField()
    is_duplicate = serializers.BooleanField()


class SubmissionJobSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)


class SubmissionCandidateSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    current_title = serializers.CharField(read_only=True)


class RecruiterSubmissionSerializer(serializers.ModelSerializer[Application]):
    job = SubmissionJobSerializer(read_only=True)
    candidate = SubmissionCandidateSerializer(read_only=True)
    first_submitted_at = serializers.SerializerMethodField()

    class Meta:
        model = Application
        fields = (
            "id",
            "job",
            "candidate",
            "stage",
            "submitted_at",
            "stage_changed_at",
            "is_duplicate",
            "first_submitted_at",
            "cv_filename",
        )
        read_only_fields = fields

    def get_first_submitted_at(self, application: Application) -> str | None:
        earlier = application.duplicate_of
        return earlier.submitted_at.isoformat() if earlier is not None else None
