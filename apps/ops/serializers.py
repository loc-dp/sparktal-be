from collections.abc import Sequence
from typing import Any
from uuid import UUID

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.companies.models import Company
from apps.jobs.bounty import (
    BOUNTY_AMOUNT_MESSAGE,
    BOUNTY_PCT_MESSAGE,
    BOUNTY_TYPE_MESSAGE,
    clean_bounty,
)
from apps.jobs.models import BountyType, Job, JobInvite
from apps.jobs.serializers import BRIEF_FIELDS
from apps.recruiters.models import RecruiterProfile, RecruiterStatus

UNKNOWN_RECRUITER = "Unknown recruiter."
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


class OpsInviteCountsSerializer(serializers.Serializer[dict[str, int]]):
    pending = serializers.IntegerField()
    accepted = serializers.IntegerField()
    declined = serializers.IntegerField()


class OpsJobListItemSerializer(serializers.ModelSerializer[Job]):
    """A job as Sparktal staff list it."""

    # The dashboard queue and `GET /ops/jobs` share this row. The invite and assignment numbers
    # come from `annotate_ops_jobs`, and the bounty may be empty: a job in the queue has none yet.
    company = OpsCompanyRefSerializer(read_only=True)
    team_name = serializers.CharField(source="team.name", read_only=True, allow_null=True)
    invites = serializers.SerializerMethodField()
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
            "invites",
            "recruiters_working",
        )
        read_only_fields = fields

    @extend_schema_field(OpsInviteCountsSerializer)
    def get_invites(self, job: Job) -> dict[str, int]:
        return {
            "pending": job.invites_pending,  # type: ignore[attr-defined]
            "accepted": job.invites_accepted,  # type: ignore[attr-defined]
            "declined": job.invites_declined,  # type: ignore[attr-defined]
        }


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

    # Not `required`: a missing note and a blank one get the same sentence from `reject_recruiter`.
    note = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text="The reason the recruiter is told. A blank or missing note is a 400.",
    )


class OpsInviteSerializer(serializers.ModelSerializer[JobInvite]):
    """An offer Sparktal made to one recruiter, and what came of it."""

    recruiter = OpsRecruiterRefSerializer(read_only=True)

    class Meta:
        model = JobInvite
        fields = ("id", "recruiter", "status", "decline_reason", "created_at", "responded_at")
        read_only_fields = fields


class OpsJobDetailSerializer(serializers.ModelSerializer[Job]):
    """One job as Sparktal staff read it: the whole JD and brief, the bounty and the channels."""

    # Read from `ops_job_detail_queryset` only. The JD and brief fields are the ones the
    # marketplace detail shows a recruiter (a test pins that), without the recruiter's own state.
    company = OpsCompanyRefSerializer(read_only=True)
    team_name = serializers.CharField(source="team.name", read_only=True, allow_null=True)
    location_label = serializers.CharField(read_only=True)
    recruiters_working = serializers.IntegerField(read_only=True)
    invites = OpsInviteSerializer(many=True, read_only=True)

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
            "invites",
        )
        read_only_fields = fields


def approved_recruiters(recruiter_ids: Sequence[UUID]) -> list[RecruiterProfile]:
    """The recruiters with these ids, in the order given and each once.

    A recruiter who is not approved can't be invited (spec §10), so to ops they do not exist.
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

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return {
            **attrs,
            **clean_bounty(
                attrs["bounty_type"], attrs.get("bounty_pct"), attrs.get("bounty_amount")
            ),
        }


class OpsDistributeSerializer(OpsBountySerializer):
    """Set the bounty and take a job out of the queue."""

    # On the marketplace, to invited recruiters, or both: `distribute_job` wants at least one.

    on_marketplace = serializers.BooleanField()
    recruiter_ids = serializers.ListField(child=serializers.UUIDField())

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = super().validate(attrs)
        return {**attrs, "recruiters": approved_recruiters(attrs["recruiter_ids"])}


class OpsChannelsSerializer(serializers.Serializer[dict[str, Any]]):
    """Change who a live job reaches."""

    on_marketplace = serializers.BooleanField(required=False)
    invite_recruiter_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    cancel_invite_ids = serializers.ListField(child=serializers.UUIDField(), required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        return {
            **attrs,
            "invite_recruiters": approved_recruiters(attrs.get("invite_recruiter_ids", [])),
        }


class OpsOverviewSerializer(serializers.Serializer[dict[str, Any]]):
    companies = serializers.IntegerField()
    active_jobs = serializers.IntegerField()
    queued_jobs = serializers.IntegerField()
    pending_recruiters = serializers.IntegerField()
    cvs_submitted = serializers.IntegerField()
    hires = serializers.IntegerField()
    queue = OpsJobListItemSerializer(many=True)
