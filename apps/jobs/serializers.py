from dataclasses import asdict
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import models, transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.relations import PKOnlyObject

from apps.candidates.stages import STAGE_ORDER
from apps.companies.access import company_of, membership_of
from apps.companies.models import CompanyRole, Office, Team
from apps.core.documents import validate_document
from apps.jobs.access import NOT_YOURS, JobPermissions, job_permissions
from apps.jobs.functions import guess_function
from apps.jobs.lifecycle import (
    BRIEF_REQUIRED_MESSAGES,
    REQUIRED_MESSAGES,
    missing_fields,
    post_job,
    submit_job,
)
from apps.jobs.models import (
    BOUNTY_PCT_MAX,
    BOUNTY_PCT_MIN,
    PRIVATE_STATUSES,
    BountyType,
    ChangeKind,
    ChangeStatus,
    JdParse,
    Job,
    JobChangeRequest,
    JobFunction,
    JobStatus,
    WorkModel,
)

MAX_SKILLS = 20

LINE_FIELDS = (
    "roles",
    "qualifications",
    "nice_to_have",
    "benefits",
    "allowances",
    "core_competencies",
)
# The SPARKTAL JOB BRIEF answers beyond the posting itself, in the brief's order.
BRIEF_FIELDS = (
    "working_hours",
    "reporting_line",
    "role_type",
    "has_internal_candidate",
    "search_duration",
    "target_start_date",
    "interview_rounds_count",
    "interview_process_details",
    "languages",
    "core_competencies",
    "probation_expectations",
    "salary_negotiable",
    "allowances",
)
# What candidates may see of the brief: nothing about the company's search or rivals.
PUBLIC_BRIEF_FIELDS = (
    "working_hours",
    "languages",
    "core_competencies",
    "interview_rounds_count",
    "interview_process_details",
    "salary_negotiable",
    "allowances",
)


def clean_lines(value: str) -> str:
    """Store one item per line without bullets (prototype `toList`)."""
    lines = [line.strip().lstrip("•-* ").strip() for line in (value or "").splitlines()]
    return "\n".join(line for line in lines if line)


PICK_TEAM_MESSAGE = "Pick the team this job belongs to."
PRIVATE_JOB_TEAM_MESSAGE = "A draft stays with its author's team until it is posted."


def _team_pick_missing(team: Team | None, company_id: Any) -> bool:
    """An admin may leave a job team-less only in a company that has no teams (R11)."""
    return team is None and Team.objects.filter(company_id=company_id).exists()


def _private_to_a_member(job: Job) -> bool:
    """A draft or sent job whose author is not an admin: it lives in the author's team."""
    if job.status not in PRIVATE_STATUSES:
        return False
    author = membership_of(job.created_by)
    return author is None or author.role != CompanyRole.ADMIN


def _set_changed(job: Job, values: dict[str, Any]) -> list[str]:
    """Set `values` on `job`; the names of the fields whose value actually changes."""
    changed: list[str] = []
    for name, value in values.items():
        new = value.pk if isinstance(value, models.Model) else value
        if job.serializable_value(name) != new:
            setattr(job, name, value)
            changed.append(name)
    return changed


def _blank_message(field: str) -> dict[str, str]:
    message = REQUIRED_MESSAGES[field]
    return {"blank": message, "required": message, "null": message}


class JobPermissionsSerializer(serializers.Serializer[Any]):
    """What the signed-in member may do to this job (spec §4)."""

    edit = serializers.BooleanField()
    submit = serializers.BooleanField()
    withdraw = serializers.BooleanField()
    post = serializers.BooleanField()
    close = serializers.BooleanField()
    reopen = serializers.BooleanField()
    delete = serializers.BooleanField()
    propose = serializers.BooleanField()
    review_proposals = serializers.BooleanField()


class JobIntent(models.TextChoices):
    SAVE = "save", "Save"
    SUBMIT = "submit", "Submit"
    POST = "post", "Post"


class JobSerializer(serializers.ModelSerializer[Job]):
    location_label = serializers.CharField(read_only=True)
    bounty_pct = serializers.IntegerField(required=False, allow_null=True)
    # Left out, the server picks it from the title and skills.
    function = serializers.ChoiceField(choices=JobFunction.choices, required=False)
    can_edit = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()
    # Only `create` / `update` act on it, so validating a payload has no side effects.
    intent = serializers.ChoiceField(
        choices=JobIntent.choices, default=JobIntent.SAVE, write_only=True
    )
    team = serializers.PrimaryKeyRelatedField(
        queryset=Team.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = Job
        fields = (
            "id",
            "team",
            "intent",
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
            *BRIEF_FIELDS,
            "status",
            "note_for_recruiters",
            "share_token",
            "created_at",
            "published_at",
            "closed_at",
            "can_edit",
            "permissions",
        )
        read_only_fields = (
            "id",
            "status",
            "share_token",
            "created_at",
            "published_at",
            "closed_at",
            "location_label",
            "can_edit",
            "permissions",
        )
        extra_kwargs = {  # noqa: RUF012
            field: {"error_messages": _blank_message(field)} for field in REQUIRED_MESSAGES
        }

    def validate_languages(self, languages: list[str]) -> list[str]:
        return list(dict.fromkeys(languages))

    def _permissions(self, job: Job) -> JobPermissions:
        request = self.context.get("request")
        if request is None:
            return JobPermissions.NONE
        cache: dict[tuple[Any, str], JobPermissions] = self.__dict__.setdefault("_cache", {})
        key = (job.pk, job.status)
        if key not in cache:
            cache[key] = job_permissions(request.user, job)
        return cache[key]

    def get_can_edit(self, job: Job) -> bool:
        return self._permissions(job).edit

    @extend_schema_field(JobPermissionsSerializer)
    def get_permissions(self, job: Job) -> dict[str, bool]:
        return asdict(self._permissions(job))

    def validate_team(self, team: Team | None) -> Team | None:
        membership = membership_of(self.context["request"].user)
        if membership is None or membership.role != CompanyRole.ADMIN:
            return team  # Ignored for everyone else, so it cannot be an error for them.
        if _team_pick_missing(team, membership.company_id):
            raise serializers.ValidationError(PICK_TEAM_MESSAGE)
        if team is None:
            return team
        company = company_of(self.context["request"].user)
        if company is None or team.company_id != company.pk:
            raise serializers.ValidationError("Select a team that belongs to your company.")
        job = self.instance
        if job is not None and team.pk != job.team_id and _private_to_a_member(job):
            # R22: moved away, the author could no longer see their own draft.
            raise serializers.ValidationError(PRIVATE_JOB_TEAM_MESSAGE)
        return team

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

        for field in LINE_FIELDS:
            if field in attrs:
                attrs[field] = clean_lines(attrs[field])
        # A draft only needs a title; sending, posting and any job already past that need it all.
        full = attrs.get("intent", JobIntent.SAVE) != JobIntent.SAVE or (
            instance is not None and instance.status not in PRIVATE_STATUSES
        )
        if "title" in attrs and not str(attrs["title"]).strip():
            raise serializers.ValidationError({"title": [REQUIRED_MESSAGES["title"]]})
        if full:
            every_answer = instance is None or instance.status in PRIVATE_STATUSES
            values = {
                field: current(field) for field in (*REQUIRED_MESSAGES, *BRIEF_REQUIRED_MESSAGES)
            }
            errors = missing_fields(
                {
                    **values,
                    "work_model": current("work_model", WorkModel.ONSITE),
                    "office": current("office"),
                },
                brief=every_answer,
            )
            if not every_answer:
                # A job posted before the brief may lack its answers until they are edited.
                for field, message in BRIEF_REQUIRED_MESSAGES.items():
                    if field in attrs and not attrs[field]:
                        errors[field] = [message]
            if errors:
                raise serializers.ValidationError(errors)

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

        if current("work_model", WorkModel.ONSITE) == WorkModel.REMOTE:
            attrs["office"] = None

        return attrs

    def create(self, validated_data: dict[str, Any]) -> Job:
        request = self.context["request"]
        membership = membership_of(request.user)
        assert membership is not None, "CanAddJob already rejected non-members"
        intent = validated_data.pop("intent", JobIntent.SAVE)
        team = validated_data.pop("team", None)
        if membership.role == CompanyRole.ADMIN:
            if _team_pick_missing(team, membership.company_id):
                raise serializers.ValidationError({"team": [PICK_TEAM_MESSAGE]})
        else:
            team = membership.team
        validated_data.setdefault(
            "function", guess_function(validated_data["title"], validated_data.get("skills", []))
        )
        with transaction.atomic():
            job = Job.objects.create(
                company_id=membership.company_id,
                team=team,
                created_by=request.user,
                status=JobStatus.DRAFT,
                **validated_data,
            )
            return self._apply_intent(job, intent)

    def update(self, instance: Job, validated_data: dict[str, Any]) -> Job:
        actor = self.context["request"].user
        with transaction.atomic():
            # `instance` was read without a lock: a delete, close or post may have landed since.
            job = Job.objects.select_for_update().filter(pk=instance.pk).first()
            if job is None or not job_permissions(actor, job).edit:
                raise PermissionDenied(NOT_YOURS)
            if job.updated_at != instance.updated_at:
                # The payload was checked against the job as it was; check it as it is now.
                fresh = type(self)(
                    job, data=self.initial_data, partial=self.partial, context=self.context
                )
                fresh.is_valid(raise_exception=True)
                validated_data = dict(fresh.validated_data)
            intent = validated_data.pop("intent", JobIntent.SAVE)
            membership = membership_of(actor)
            if membership is None or membership.role != CompanyRole.ADMIN:
                validated_data.pop("team", None)
            # A function the company picked stays when the title changes; a guessed one follows.
            guessed = job.function == guess_function(job.title, job.skills)
            changed = _set_changed(job, validated_data)
            retitled = "title" in validated_data or "skills" in validated_data
            if "function" not in validated_data and retitled and guessed:
                changed += _set_changed(job, {"function": guess_function(job.title, job.skills)})
            job.save(update_fields=[*changed, "updated_at"])
            if "team" in changed:
                # R22: the requesters stay in the old team and can no longer see the job.
                job.change_requests.filter(status=ChangeStatus.PENDING).update(
                    status=ChangeStatus.WITHDRAWN
                )
            return self._apply_intent(job, intent)

    def _apply_intent(self, job: Job, intent: str) -> Job:
        actor = self.context["request"].user
        if intent == JobIntent.SUBMIT:
            return submit_job(job, actor=actor)
        if intent == JobIntent.POST:
            return post_job(job, actor=actor)
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
            *PUBLIC_BRIEF_FIELDS,
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
    team_name = serializers.CharField(source="team.name", read_only=True, allow_null=True)
    # Proposals the viewer may see on this job: an admin all, a requester their own, others 0.
    pending_changes = serializers.IntegerField(read_only=True)

    class Meta:
        model = Job
        fields = (
            "id",
            "title",
            "function",
            "status",
            "team_name",
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
            "pending_changes",
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
    draft = serializers.IntegerField()
    submitted = serializers.IntegerField()
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
    id = serializers.UUIDField()
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
            *BRIEF_FIELDS,
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


class ChangeRequestPersonSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    full_name = serializers.CharField()


class ChangeRequestJobSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    title = serializers.CharField()


class ChangeRequestSerializer(serializers.ModelSerializer[JobChangeRequest]):
    """A proposal, with the job's current values beside the proposed ones."""

    job = ChangeRequestJobSerializer(read_only=True)
    changes = serializers.DictField(read_only=True)
    current = serializers.SerializerMethodField()
    requested_by = ChangeRequestPersonSerializer(read_only=True)
    reviewed_by = ChangeRequestPersonSerializer(read_only=True, allow_null=True)

    class Meta:
        model = JobChangeRequest
        fields = (
            "id",
            "job",
            "kind",
            "changes",
            "current",
            "status",
            "review_note",
            "requested_by",
            "reviewed_by",
            "created_at",
            "reviewed_at",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.DictField())
    def get_current(self, change: JobChangeRequest) -> dict[str, Any]:
        # `many=True` reuses one child for every row, so this is built once per list.
        if "_job_fields" not in self.__dict__:
            self.__dict__["_job_fields"] = JobSerializer().fields
        fields = self.__dict__["_job_fields"]
        current: dict[str, Any] = {}
        for key in change.changes:
            if key not in fields:
                continue
            attribute = fields[key].get_attribute(change.job)
            # DRF's own rule: a missing value is null, not whatever `to_representation` makes.
            empty = attribute is None or (
                isinstance(attribute, PKOnlyObject) and attribute.pk is None
            )
            current[key] = None if empty else fields[key].to_representation(attribute)
        return current


class ChangeRequestCreateSerializer(serializers.Serializer[Any]):
    kind = serializers.ChoiceField(choices=ChangeKind.choices)
    changes = serializers.DictField(required=False)


class ChangeRequestDecisionSerializer(serializers.Serializer[Any]):
    # Required by `reject_change`, which answers with the sentence the form shows.
    note = serializers.CharField(required=False, allow_blank=True, default="")
