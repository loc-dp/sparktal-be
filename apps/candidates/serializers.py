from __future__ import annotations

from typing import Any, ClassVar

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import UploadedFile
from rest_framework import serializers

from apps.candidates.cv_files import validate_cv
from apps.candidates.models import Application, Candidate, CvParse


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
            }
        }

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
