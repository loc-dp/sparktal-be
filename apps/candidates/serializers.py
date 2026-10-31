from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any, ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import APIException

from apps.candidates.cv_fields import MAX_AGE, MIN_AGE, is_working_age
from apps.candidates.cv_files import validate_cv
from apps.candidates.models import (
    Application,
    Candidate,
    CvParse,
    CvParseReader,
    CvParseStatus,
)
from apps.candidates.parsing import MAX_YEARS
from apps.recruiters.models import RecruiterProfile


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


class PoolJobSerializer(serializers.Serializer[object]):
    id = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)


class CompanyPoolApplicationSerializer(serializers.ModelSerializer[Application]):
    job = PoolJobSerializer(read_only=True)
    recruiter = ApplicationRecruiterSerializer(read_only=True)

    class Meta:
        model = Application
        fields = (
            "id",
            "job",
            "recruiter",
            "stage",
            "submitted_at",
            "stage_changed_at",
            "cv_filename",
            "is_duplicate",
        )
        read_only_fields = fields


class CompanyPoolEntrySerializer(serializers.Serializer[object]):
    """One person as the company knows them: what their CV says, never how to reach them."""

    id = serializers.UUIDField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    current_title = serializers.CharField(read_only=True)
    location = serializers.CharField(read_only=True)
    years_experience = serializers.IntegerField(read_only=True, allow_null=True)
    skills = serializers.ListField(child=serializers.CharField(), read_only=True)
    last_submitted_at = serializers.DateTimeField(read_only=True)
    applications = CompanyPoolApplicationSerializer(many=True, read_only=True)


class CvParseSerializer(serializers.ModelSerializer[CvParse]):
    class Meta:
        model = CvParse
        fields = ("id", "status", "fields", "error", "filename", "created_at", "finished_at")
        read_only_fields = fields


# The quota and the reuse both look back one day; the purge keeps parses exactly that long.
CV_PARSE_WINDOW = timedelta(hours=24)


class CvParseQuotaExceeded(APIException):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = (
        "You've hit today's CV reading limit. Fill the form in yourself, or try again later."
    )
    default_code = "cv_parse_quota"


def sha256_of(upload: UploadedFile[Any]) -> str:
    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    return digest.hexdigest()


def earlier_read(owner: RecruiterProfile, file_sha256: str) -> CvParse | None:
    """This recruiter's last good Claude read of the same file within the window.

    A rules-only read is not reused: it usually means Claude was down, so a new upload
    deserves a fresh try.
    """
    return (
        CvParse.objects.filter(
            owner=owner,
            file_sha256=file_sha256,
            status=CvParseStatus.DONE,
            reader=CvParseReader.CLAUDE,
            created_at__gte=timezone.now() - CV_PARSE_WINDOW,
        )
        .order_by("-created_at")
        .first()
    )


def parses_started(owner: RecruiterProfile) -> int:
    """Parses that ran a reader in the window; reused ones cost nothing and do not count."""
    return CvParse.objects.filter(
        owner=owner, reused=False, created_at__gte=timezone.now() - CV_PARSE_WINDOW
    ).count()


class CvParseCreateSerializer(serializers.Serializer[CvParse]):
    file = serializers.FileField(write_only=True)

    def validate_file(self, upload: UploadedFile[Any]) -> UploadedFile[Any]:
        try:
            validate_cv(upload)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from error
        return upload

    def create(self, validated_data: dict[str, Any]) -> CvParse:
        """A queued parse, or a finished copy when this file was read before."""
        upload = validated_data["file"]
        owner = self.context["recruiter"]
        filename = upload.name or "cv.pdf"
        file_sha256 = sha256_of(upload)
        earlier = earlier_read(owner, file_sha256)
        if earlier is not None:
            now = timezone.now()
            return CvParse.objects.create(
                owner=owner,
                filename=filename,
                file_sha256=file_sha256,
                reused=True,
                status=CvParseStatus.DONE,
                fields=earlier.fields,
                reader=earlier.reader,
                started_at=now,
                finished_at=now,
            )
        if parses_started(owner) >= settings.CV_PARSE_DAILY_LIMIT:
            raise CvParseQuotaExceeded
        return CvParse.objects.create(
            owner=owner, file=upload, filename=filename, file_sha256=file_sha256
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
        fields: tuple[str, ...] = (
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
        read_only_fields: tuple[str, ...] = fields

    def get_first_submitted_at(self, application: Application) -> str | None:
        earlier = application.duplicate_of
        return earlier.submitted_at.isoformat() if earlier is not None else None


class RecruiterSubmissionDetailSerializer(RecruiterSubmissionSerializer):
    """One submission with the notes the recruiter sent alongside the CV."""

    class Meta(RecruiterSubmissionSerializer.Meta):
        fields = (
            *RecruiterSubmissionSerializer.Meta.fields,
            "reason_for_leaving",
            "motivation",
            "other_notes",
            "consultant_note",
        )
        read_only_fields = fields
