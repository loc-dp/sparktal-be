from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import UploadedFile
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.candidates.stages import STAGE_ORDER
from apps.companies.access import company_of
from apps.companies.models import Office
from apps.core.documents import validate_document
from apps.jobs.functions import guess_function
from apps.jobs.models import BOUNTY_PCT_MAX, BOUNTY_PCT_MIN, BountyType, JdParse, Job, WorkModel

MAX_SKILLS = 20

REQUIRED_MESSAGES = {
    "title": "Enter a job title",
    "why_we_need_you": "Tell recruiters why this role exists",
    "roles": "List the main responsibilities",
    "qualifications": "Add the must-have qualifications",
}


def clean_lines(value: str) -> str:
    """Store one item per line without bullets (prototype `toList`)."""
    lines = [line.strip().lstrip("•-* ").strip() for line in (value or "").splitlines()]
    return "\n".join(line for line in lines if line)


def _blank_message(field: str) -> dict[str, str]:
    message = REQUIRED_MESSAGES[field]
    return {"blank": message, "required": message, "null": message}


class JobSerializer(serializers.ModelSerializer[Job]):
    location_label = serializers.CharField(read_only=True)
    bounty_pct = serializers.IntegerField(required=False, allow_null=True)
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = Job
        fields = (
            "id",
            "title",
            "function",
            "why_we_need_you",
            "roles",
            "qualifications",
            "nice_to_have",
            "benefits",
            "salary_min",
            "salary_max",
            "pay_basis",
            "work_model",
            "office",
            "location_label",
            "bounty_type",
            "bounty_pct",
            "bounty_amount",
            "skills",
            "status",
            "note_for_recruiters",
            "share_token",
            "created_at",
            "published_at",
            "closed_at",
            "can_edit",
        )
        read_only_fields = (
            "id",
            "function",
            "status",
            "share_token",
            "created_at",
            "published_at",
            "closed_at",
            "location_label",
            "can_edit",
        )
        extra_kwargs = {  # noqa: RUF012
            field: {"error_messages": _blank_message(field)} for field in REQUIRED_MESSAGES
        }

    def get_can_edit(self, job: Job) -> bool:
        from apps.jobs.access import can_change_job

        request = self.context.get("request")
        return bool(request and can_change_job(request.user, job))

    def validate_office(self, office: Office | None) -> Office | None:
        if office is None:
            return office
        request = self.context["request"]
        company = company_of(request.user)
        if company is None or office.company_id != company.pk:
            raise serializers.ValidationError("Select an office that belongs to your company.")
        return office

    def validate_skills(self, skills: list[str]) -> list[str]:
        seen: dict[str, str] = {}
        for skill in skills:
            cleaned = skill.strip()
            if not cleaned:
                continue
            if len(cleaned) > 40:
                raise serializers.ValidationError("Each skill can be at most 40 characters.")
            seen.setdefault(cleaned.lower(), cleaned)
        if len(seen) > MAX_SKILLS:
            raise serializers.ValidationError(f"Up to {MAX_SKILLS} skills.")
        return list(seen.values())

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        instance = self.instance

        def current(field: str, default: Any = None) -> Any:
            return attrs.get(field, getattr(instance, field, default))

        for field in ("roles", "qualifications", "nice_to_have", "benefits"):
            if field in attrs:
                attrs[field] = clean_lines(attrs[field])
        for field, message in REQUIRED_MESSAGES.items():
            if field in attrs and not str(attrs[field]).strip():
                raise serializers.ValidationError({field: [message]})

        salary_min: Decimal | None = current("salary_min")
        salary_max: Decimal | None = current("salary_max")
        if salary_min is not None and salary_max is not None and salary_max < salary_min:
            raise serializers.ValidationError(
                {"salary_max": ["Max salary must be at least the min"]}
            )

        bounty_type = current("bounty_type", BountyType.PCT)
        if bounty_type == BountyType.FIXED:
            if not current("bounty_amount"):
                raise serializers.ValidationError(
                    {"bounty_amount": ["Enter the fixed bounty amount"]}
                )
            attrs["bounty_pct"] = None
        else:
            pct = current("bounty_pct", 15)
            if pct is None or not BOUNTY_PCT_MIN <= int(pct) <= BOUNTY_PCT_MAX:
                raise serializers.ValidationError(
                    {
                        "bounty_pct": [
                            f"Bounty must be between {BOUNTY_PCT_MIN} and {BOUNTY_PCT_MAX}%"
                        ]
                    }
                )
            attrs["bounty_amount"] = None

        work_model = current("work_model", WorkModel.ONSITE)
        if work_model != WorkModel.REMOTE and current("office") is None:
            raise serializers.ValidationError({"office": ["Select an office for this job"]})
        if work_model == WorkModel.REMOTE:
            attrs["office"] = None

        return attrs

    def create(self, validated_data: dict[str, Any]) -> Job:
        from apps.jobs.access import grant_creator_perms

        request = self.context["request"]
        company = company_of(request.user)
        assert company is not None, "CanAddJob already rejected non-members"
        job = Job.objects.create(
            company=company,
            created_by=request.user,
            function=guess_function(validated_data["title"], validated_data.get("skills", [])),
            **validated_data,
        )
        grant_creator_perms(job, request.user)
        return job

    def update(self, instance: Job, validated_data: dict[str, Any]) -> Job:
        job = super().update(instance, validated_data)
        if "title" in validated_data or "skills" in validated_data:
            job.function = guess_function(job.title, job.skills)
            job.save(update_fields=["function", "updated_at"])
        return job


class PublicJobSerializer(serializers.ModelSerializer[Job]):
    """What anyone with the share link may see: no bounty, no internal notes."""

    company = serializers.SerializerMethodField()
    location_label = serializers.CharField(read_only=True)

    class Meta:
        model = Job
        fields = (
            "id",
            "title",
            "function",
            "why_we_need_you",
            "roles",
            "qualifications",
            "nice_to_have",
            "benefits",
            "salary_min",
            "salary_max",
            "pay_basis",
            "work_model",
            "location_label",
            "skills",
            "published_at",
            "company",
        )

    def get_company(self, job: Job) -> dict[str, Any]:
        request = self.context.get("request")
        logo = job.company.logo
        return {
            "name": job.company.name,
            "about": job.company.about,
            "logo_url": request.build_absolute_uri(logo.url) if (logo and request) else None,
        }


class JobListItemSerializer(serializers.ModelSerializer[Job]):
    """The My jobs row: every number comes from `annotate_job_metrics`."""

    cv_count = serializers.IntegerField(read_only=True)
    new_count = serializers.IntegerField(read_only=True)
    days_open = serializers.IntegerField(read_only=True)
    days_to_onboard = serializers.IntegerField(read_only=True, allow_null=True)
    current_stage = serializers.SerializerMethodField()
    location_label = serializers.CharField(read_only=True)
    bounty_label = serializers.SerializerMethodField()

    class Meta:
        model = Job
        fields = (
            "id",
            "title",
            "function",
            "status",
            "work_model",
            "location_label",
            "created_at",
            "published_at",
            "share_token",
            "cv_count",
            "new_count",
            "current_stage",
            "days_open",
            "days_to_onboard",
            "bounty_label",
        )

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_current_stage(self, job: Job) -> str | None:
        rank = getattr(job, "stage_rank", None)
        if rank is None or rank < 0:
            return None
        stage: str = STAGE_ORDER[rank]
        return stage

    @extend_schema_field(serializers.CharField())
    def get_bounty_label(self, job: Job) -> str:
        if job.bounty_type == BountyType.FIXED and job.bounty_amount:
            grouped = f"{int(job.bounty_amount):,}".replace(",", ".")
            return f"{grouped} ₫ fixed"
        return f"{job.bounty_pct or 0}% of annual salary"


class JobCountsSerializer(serializers.Serializer[dict[str, int]]):
    pending = serializers.IntegerField()
    active = serializers.IntegerField()
    closed = serializers.IntegerField()


class JobKpisSerializer(serializers.Serializer[dict[str, int]]):
    days_open = serializers.IntegerField()
    applicants = serializers.IntegerField()
    screen = serializers.IntegerField()
    interviewing = serializers.IntegerField()
    offer = serializers.IntegerField()


class NewApplicantSerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.UUIDField()
    candidate_name = serializers.CharField()
    current_title = serializers.CharField()
    recruiter_nickname = serializers.CharField()
    submitted_at = serializers.DateTimeField()
    is_duplicate = serializers.BooleanField()


class RejectionSerializer(serializers.Serializer[dict[str, object]]):
    candidate_name = serializers.CharField()
    reason = serializers.CharField(allow_blank=True)
    rejected_at = serializers.DateTimeField()


class RecruiterOnJobSerializer(serializers.Serializer[dict[str, object]]):
    id = serializers.UUIDField()
    nickname = serializers.CharField()
    cv_count = serializers.IntegerField()


class JobOverviewSerializer(serializers.Serializer[dict[str, object]]):
    kpis = JobKpisSerializer()
    new_applicants = NewApplicantSerializer(many=True)
    rejection_log = RejectionSerializer(many=True)
    recruiters = RecruiterOnJobSerializer(many=True)
    note_for_recruiters = serializers.CharField(allow_blank=True)


class MarketplaceJobListItemSerializer(serializers.ModelSerializer[Job]):
    company = serializers.SerializerMethodField()
    location_label = serializers.CharField(read_only=True)
    joined = serializers.BooleanField(read_only=True)
    submitted_count = serializers.IntegerField(read_only=True)
    recruiters_working = serializers.IntegerField(read_only=True)

    class Meta:
        model = Job
        fields = (
            "id",
            "title",
            "function",
            "company",
            "work_model",
            "location_label",
            "salary_min",
            "salary_max",
            "pay_basis",
            "bounty_type",
            "bounty_pct",
            "bounty_amount",
            "skills",
            "why_we_need_you",
            "published_at",
            "created_at",
            "joined",
            "submitted_count",
            "recruiters_working",
        )

    @extend_schema_field(serializers.DictField(child=serializers.CharField()))
    def get_company(self, job: Job) -> dict[str, str]:
        return {
            "id": str(job.company_id),
            "name": job.company.name,
            "industry": job.company.get_industry_display(),
        }


class MarketplaceJobSerializer(MarketplaceJobListItemSerializer):
    channel_id = serializers.SerializerMethodField()

    class Meta:
        model = Job
        fields = (
            *MarketplaceJobListItemSerializer.Meta.fields,
            "roles",
            "qualifications",
            "nice_to_have",
            "benefits",
            "note_for_recruiters",
            "channel_id",
        )

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_channel_id(self, job: Job) -> str | None:
        from apps.chat.models import Channel

        recruiter = self.context.get("recruiter")
        if recruiter is None or not getattr(job, "joined", False):
            return None
        channel = Channel.objects.filter(job=job, recruiter=recruiter).only("id").first()
        return str(channel.id) if channel else None


class JoinResultSerializer(serializers.Serializer[dict[str, Any]]):
    assignment_id = serializers.UUIDField()
    channel_id = serializers.UUIDField()
    joined = serializers.BooleanField()


class LeaveResultSerializer(serializers.Serializer[dict[str, Any]]):
    assignment_id = serializers.UUIDField()
    joined = serializers.BooleanField()
    left_at = serializers.DateTimeField()


JD_TYPE_MESSAGE = "Only PDF, DOCX or DOC files can be uploaded as a job description."
JD_SIZE_MESSAGE = "Job description files must be 10 MB or smaller."


class JdParseSerializer(serializers.ModelSerializer[JdParse]):
    class Meta:
        model = JdParse
        fields = ("id", "status", "fields", "error", "filename", "created_at", "finished_at")
        read_only_fields = fields


class JdParseCreateSerializer(serializers.Serializer[JdParse]):
    file = serializers.FileField(write_only=True)

    def validate_file(self, upload: "UploadedFile[Any]") -> "UploadedFile[Any]":
        try:
            validate_document(upload, JD_TYPE_MESSAGE, JD_SIZE_MESSAGE)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from error
        return upload

    def create(self, validated_data: dict[str, Any]) -> JdParse:
        upload = validated_data["file"]
        return JdParse.objects.create(
            company=self.context["company"],
            uploaded_by=self.context["user"],
            file=upload,
            filename=upload.name or "jd.pdf",
        )


class JdParseAvailabilitySerializer(serializers.Serializer[dict[str, bool]]):
    enabled = serializers.BooleanField()
