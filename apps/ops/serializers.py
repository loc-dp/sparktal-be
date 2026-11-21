from collections.abc import Sequence
from datetime import date
from typing import Any
from uuid import UUID

from django.utils import timezone
from rest_framework import serializers

from apps.companies.models import Company
from apps.hiring.models import Placement
from apps.hiring.serializers import RecruiterPlacementSerializer
from apps.jobs.bounty import (
    BOUNTY_AMOUNT_MESSAGE,
    BOUNTY_PCT_MESSAGE,
    BOUNTY_TYPE_MESSAGE,
    clean_bounty,
)
from apps.jobs.distribution import NEEDS_RECRUITER
from apps.jobs.models import GUARANTEE_DAYS_MAX, BountyType, Job, JobAssignment
from apps.jobs.serializers import BRIEF_FIELDS
from apps.recruiters.models import RecruiterProfile, RecruiterStatus

UNKNOWN_RECRUITER = "Unknown recruiter."
PAID_IN_THE_FUTURE = "The payment date cannot be in the future."
# `Job.bounty_amount` is numeric(15, 0): whole dong, up to this many digits.
BOUNTY_AMOUNT_DIGITS = 15
BOUNTY_AMOUNT_SIZE_MESSAGE = f"Enter a whole amount of up to {BOUNTY_AMOUNT_DIGITS} digits."


class OpsCompanyRefSerializer(serializers.ModelSerializer[Company]):
    class Meta:
        model = Company
        fields = ("id", "name")
        read_only_fields = fields


class OpsCompanySerializer(serializers.ModelSerializer[Company]):
    """A company as Sparktal staff list it, with its people, teams and live and queued jobs."""

    # Read from `annotate_ops_companies` only: the counts are its annotations.
    profile_completed = serializers.BooleanField(read_only=True)
    members = serializers.IntegerField(source="member_count", read_only=True)
    teams = serializers.IntegerField(source="team_count", read_only=True)
    active_jobs = serializers.IntegerField(source="active_job_count", read_only=True)
    queued_jobs = serializers.IntegerField(source="queued_job_count", read_only=True)

    class Meta:
        model = Company
        fields = (
            "id",
            "name",
            "created_at",
            "profile_completed",
            "members",
            "teams",
            "active_jobs",
            "queued_jobs",
        )
        read_only_fields = fields


class OpsCompanyCreateSerializer(serializers.Serializer[dict[str, Any]]):
    """Open a company account and invite its first admin."""

    # `provision_company` judges the name (1 to 120 characters once trimmed), so every bad name
    # gets its one sentence, keyed `name`.
    name = serializers.CharField(allow_blank=True, help_text="1 to 120 characters once trimmed.")
    admin_email = serializers.EmailField(max_length=254)
    admin_full_name = serializers.CharField(max_length=120)


class OpsJobListItemSerializer(serializers.ModelSerializer[Job]):
    """A job as Sparktal staff list it."""

    # The dashboard queue and `GET /ops/jobs` share this row. The assignment count comes from
    # `annotate_ops_jobs`, and the bounty may be empty: a job in the queue has none yet.
    company = OpsCompanyRefSerializer(read_only=True)
    team_name = serializers.CharField(source="team.name", read_only=True, allow_null=True)
    recruiters_working = serializers.IntegerField(read_only=True)

    class Meta:
        model = Job
        fields = (
            "id",
            "title",
            "company",
            "team_name",
            "status",
            "posted_at",
            "published_at",
            "bounty_type",
            "bounty_pct",
            "bounty_amount",
            "on_marketplace",
            "recruiters_working",
        )
        read_only_fields = fields


class OpsRecruiterRefSerializer(serializers.ModelSerializer[RecruiterProfile]):
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = RecruiterProfile
        fields = ("id", "nickname", "email")
        read_only_fields = fields


class OpsRecruiterSerializer(serializers.ModelSerializer[RecruiterProfile]):
    """A recruiter as Sparktal staff review them."""

    # Read from `annotate_ops_recruiters` only: `active_jobs` is its annotation.
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    active_jobs = serializers.IntegerField(source="active_job_count", read_only=True)

    class Meta:
        model = RecruiterProfile
        fields = (
            "id",
            "nickname",
            "full_name",
            "email",
            "status",
            "expertise",
            "years_experience",
            "linkedin",
            "bio",
            "submitted_at",
            "reviewed_at",
            "review_note",
            "active_jobs",
        )
        read_only_fields = fields


class OpsRecruiterRejectSerializer(serializers.Serializer[dict[str, Any]]):
    """Why Sparktal turns a recruiter down. They read it in the email."""

    # Not `required`, and null is let through: a missing, blank or null note all get the same
    # sentence from `reject_recruiter`.
    note = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        default="",
        help_text="The reason the recruiter is told. A blank, null or missing note is a 400.",
    )

    def validate_note(self, value: str | None) -> str:
        return value or ""


class OpsAssignmentSerializer(serializers.ModelSerializer[JobAssignment]):
    """A recruiter on a job: when they joined, when Sparktal took them off, the CVs they sent."""

    # Read from `ops_job_assignments` only: `cv_count` is its annotation.
    recruiter = OpsRecruiterRefSerializer(read_only=True)
    cv_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = JobAssignment
        fields = ("recruiter", "joined_at", "left_at", "cv_count")
        read_only_fields = fields


class OpsJobDetailSerializer(serializers.ModelSerializer[Job]):
    """One job as Sparktal staff read it: the whole JD and brief, the bounty and who works it."""

    # Read from `ops_job_detail_queryset` only. The JD and brief fields are the ones the
    # marketplace detail shows a recruiter (a test pins that), without the recruiter's own state.
    company = OpsCompanyRefSerializer(read_only=True)
    team_name = serializers.CharField(source="team.name", read_only=True, allow_null=True)
    location_label = serializers.CharField(read_only=True)
    recruiters_working = serializers.IntegerField(read_only=True)
    # The ones on the job first, oldest first, then the ones Sparktal took off.
    assignments = OpsAssignmentSerializer(many=True, read_only=True)

    class Meta:
        model = Job
        fields = (
            "id",
            "title",
            "function",
            "company",
            "team_name",
            "status",
            "posted_at",
            "published_at",
            "on_marketplace",
            "bounty_type",
            "bounty_pct",
            "bounty_amount",
            "guarantee_days",
            "why_we_need_you",
            "roles",
            "qualifications",
            "nice_to_have",
            "benefits",
            "skills",
            "work_model",
            "location_label",
            "salary_min",
            "salary_max",
            "pay_basis",
            *BRIEF_FIELDS,
            "note_for_recruiters",
            "recruiters_working",
            "assignments",
        )
        read_only_fields = fields


def approved_recruiters(recruiter_ids: Sequence[UUID]) -> list[RecruiterProfile]:
    """The recruiters with these ids, in the order given and each once.

    A recruiter who is not approved can't be assigned (spec §10), so to ops they do not exist.
    """
    wanted = list(dict.fromkeys(recruiter_ids))
    found = (
        RecruiterProfile.objects.filter(status=RecruiterStatus.APPROVED)
        .select_related("user")
        .in_bulk(wanted)
    )
    if len(found) != len(wanted):
        # One key for every list of recruiters, as the brief has it.
        raise serializers.ValidationError({"recruiter_ids": [UNKNOWN_RECRUITER]})
    return [found[pk] for pk in wanted]


class OpsBountySerializer(serializers.Serializer[dict[str, Any]]):
    """The bounty Sparktal sets on a job."""

    # `clean_bounty` is the one bounty rule, here as everywhere.
    bounty_type = serializers.ChoiceField(
        choices=BountyType.choices,
        error_messages={
            "required": BOUNTY_TYPE_MESSAGE,
            "null": BOUNTY_TYPE_MESSAGE,
            "invalid_choice": BOUNTY_TYPE_MESSAGE,
        },
    )
    # What is not a whole number gets the range sentence too, as 40 does: the field only reads
    # the value, `clean_bounty` is what judges it.
    bounty_pct = serializers.IntegerField(
        required=False,
        allow_null=True,
        error_messages={"invalid": BOUNTY_PCT_MESSAGE, "max_string_length": BOUNTY_PCT_MESSAGE},
    )
    # The same size as the column, so an amount that does not fit is a 400 on this field and
    # never a database error. Not a number at all reads as no amount, as `clean_bounty` has it.
    bounty_amount = serializers.DecimalField(
        max_digits=BOUNTY_AMOUNT_DIGITS,
        decimal_places=0,
        required=False,
        allow_null=True,
        error_messages={
            "invalid": BOUNTY_AMOUNT_MESSAGE,
            "max_string_length": BOUNTY_AMOUNT_MESSAGE,
            "max_digits": BOUNTY_AMOUNT_SIZE_MESSAGE,
            "max_decimal_places": BOUNTY_AMOUNT_SIZE_MESSAGE,
            "max_whole_digits": BOUNTY_AMOUNT_SIZE_MESSAGE,
        },
    )
    # How long a hire must last before the recruiter is paid; left out, the job keeps its own.
    guarantee_days = serializers.IntegerField(
        required=False, min_value=1, max_value=GUARANTEE_DAYS_MAX
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return {
            **attrs,
            **clean_bounty(
                attrs["bounty_type"], attrs.get("bounty_pct"), attrs.get("bounty_amount")
            ),
        }


def recruiter_ids_field() -> serializers.ListField:
    """The recruiters to put on a job: at least one."""
    return serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        error_messages={"required": NEEDS_RECRUITER, "min_length": NEEDS_RECRUITER},
    )


class OpsDistributeSerializer(OpsBountySerializer):
    """Set the bounty and take a job out of the queue, with the headhunters picked on it."""

    recruiter_ids = recruiter_ids_field()
    # Ignored while `MARKETPLACE_ENABLED` is off: `distribute_job` keeps the job off it.
    on_marketplace = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)
        return {**attrs, "recruiters": approved_recruiters(attrs["recruiter_ids"])}


class OpsAssignSerializer(serializers.Serializer[dict[str, Any]]):
    """Put more headhunters on a live job."""

    recruiter_ids = recruiter_ids_field()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return {**attrs, "recruiters": approved_recruiters(attrs["recruiter_ids"])}


class OpsChannelsSerializer(serializers.Serializer[dict[str, Any]]):
    """Put a live job on the marketplace, or take it off."""

    on_marketplace = serializers.BooleanField()


class OpsOverviewSerializer(serializers.Serializer[dict[str, Any]]):
    companies = serializers.IntegerField()
    active_jobs = serializers.IntegerField()
    queued_jobs = serializers.IntegerField()
    unstaffed_jobs = serializers.IntegerField()
    pending_recruiters = serializers.IntegerField()
    cvs_submitted = serializers.IntegerField()
    hires = serializers.IntegerField()
    queue = OpsJobListItemSerializer(many=True)


class OpsPlacementRecruiterSerializer(serializers.ModelSerializer[RecruiterProfile]):
    class Meta:
        model = RecruiterProfile
        fields = ("id", "nickname")
        read_only_fields = fields


class OpsPlacementSerializer(RecruiterPlacementSerializer):
    """A hire as Sparktal staff read it: what the recruiter sees, and who paid whom."""

    company = OpsCompanyRefSerializer(source="application.job.company", read_only=True)
    recruiter = OpsPlacementRecruiterSerializer(source="application.recruiter", read_only=True)

    class Meta(RecruiterPlacementSerializer.Meta):
        model = Placement
        fields = (*RecruiterPlacementSerializer.Meta.fields, "company", "recruiter", "payout_note")
        read_only_fields = fields


class OpsMarkPaidSerializer(serializers.Serializer[dict[str, Any]]):
    """What Sparktal paid the recruiter, and when."""

    paid_amount = serializers.DecimalField(
        max_digits=BOUNTY_AMOUNT_DIGITS, decimal_places=0, min_value=1
    )
    paid_at = serializers.DateField()
    note = serializers.CharField(allow_blank=True, required=False, default="", max_length=1000)

    def validate_paid_at(self, value: date) -> date:
        if value > timezone.localdate():
            raise serializers.ValidationError(PAID_IN_THE_FUTURE)
        return value
