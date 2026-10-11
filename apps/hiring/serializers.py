"""Payloads for the candidate detail screen."""

from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import User
from apps.candidates.models import Application, Candidate, Stage
from apps.candidates.serializers import (
    ApplicationRecruiterSerializer,
    RecruiterSubmissionDetailSerializer,
)
from apps.chat.models import BODY_MAX_LENGTH
from apps.chat.serializers import sanitise_body
from apps.companies.models import CompanyRole
from apps.hiring.cv import CvKind, cv_kind
from apps.hiring.history import stage_history
from apps.hiring.models import (
    LOCATION_MAX_LENGTH,
    NOTE_MAX_LENGTH,
    REASON_MAX_LENGTH,
    ApprovalRequest,
    CandidateNote,
    Feedback,
    Interview,
    InterviewFormat,
    Verdict,
)
from apps.hiring.workflow import Action, available_actions, last_decline, pending_approval


def requester_name(approval: ApprovalRequest) -> str:
    return approval.requested_by.full_name if approval.requested_by else "A former teammate"


class DetailJobSerializer(serializers.Serializer[object]):
    id = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)


class CandidateFactsSerializer(serializers.ModelSerializer[Candidate]):
    """What the CV says about the person; never how to reach them."""

    class Meta:
        model = Candidate
        fields: tuple[str, ...] = (
            "full_name",
            "current_title",
            "location",
            "languages",
            "skills",
            "years_experience",
            "current_salary",
            "expected_salary",
        )
        read_only_fields: tuple[str, ...] = fields


class InterviewSerializer(serializers.ModelSerializer[Interview]):
    class Meta:
        model = Interview
        fields = ("id", "round", "starts_at", "ends_at", "format", "location")
        read_only_fields = fields


class PersonSerializer(serializers.Serializer[User]):
    id = serializers.UUIDField(read_only=True)
    full_name = serializers.CharField(read_only=True)


class PendingApprovalSerializer(serializers.ModelSerializer[ApprovalRequest]):
    approver = PersonSerializer(read_only=True)
    requested_by_name = serializers.SerializerMethodField()
    can_decide = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalRequest
        fields = (
            "id",
            "approver",
            "requested_by_name",
            "stage",
            "note",
            "created_at",
            "can_decide",
        )
        read_only_fields = fields

    def get_requested_by_name(self, approval: ApprovalRequest) -> str:
        return requester_name(approval)

    def get_can_decide(self, approval: ApprovalRequest) -> bool:
        return bool(approval.approver_id == self.context["request"].user.pk)


class DeclineSerializer(serializers.ModelSerializer[ApprovalRequest]):
    approver_name = serializers.CharField(source="approver.full_name", read_only=True)
    reason = serializers.CharField(source="decision_note", read_only=True)

    class Meta:
        model = ApprovalRequest
        fields = ("id", "approver_name", "reason", "decided_at")
        read_only_fields = fields


class StageEntrySerializer(serializers.Serializer[object]):
    stage = serializers.ChoiceField(choices=Stage.choices, read_only=True)
    at = serializers.DateTimeField(read_only=True)
    by = serializers.CharField(read_only=True, allow_null=True)


def cv_kind_of(application: Application) -> str:
    return str(cv_kind(application.cv_filename))


def history_of(application: Application) -> Any:
    return StageEntrySerializer(stage_history(application), many=True).data


class ApplicationDetailSerializer(serializers.ModelSerializer[Application]):
    job = DetailJobSerializer(read_only=True)
    candidate = CandidateFactsSerializer(read_only=True)
    recruiter = ApplicationRecruiterSerializer(read_only=True)
    first_submitted_at = serializers.DateTimeField(
        source="duplicate_of.submitted_at", read_only=True, allow_null=True
    )
    cv_kind = serializers.SerializerMethodField()
    interviews = InterviewSerializer(many=True, read_only=True)
    pending_approval = serializers.SerializerMethodField()
    last_decline = serializers.SerializerMethodField()
    history = serializers.SerializerMethodField()
    actions = serializers.SerializerMethodField()

    class Meta:
        model = Application
        fields = (
            "id",
            "job",
            "candidate",
            "recruiter",
            "stage",
            "submitted_at",
            "stage_changed_at",
            "rejected_reason",
            "is_duplicate",
            "first_submitted_at",
            "cv_filename",
            "cv_kind",
            "reason_for_leaving",
            "motivation",
            "other_notes",
            "consultant_note",
            "interviews",
            "pending_approval",
            "last_decline",
            "history",
            "actions",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=CvKind.choices))
    def get_cv_kind(self, application: Application) -> str:
        return cv_kind_of(application)

    @extend_schema_field(PendingApprovalSerializer(allow_null=True))
    def get_pending_approval(self, application: Application) -> Any:
        approval = pending_approval(application)
        if approval is None:
            return None
        return PendingApprovalSerializer(approval, context=self.context).data

    @extend_schema_field(DeclineSerializer(allow_null=True))
    def get_last_decline(self, application: Application) -> Any:
        decline = last_decline(application)
        return None if decline is None else DeclineSerializer(decline).data

    @extend_schema_field(StageEntrySerializer(many=True))
    def get_history(self, application: Application) -> Any:
        return history_of(application)

    @extend_schema_field(
        serializers.ListField(child=serializers.ChoiceField(choices=Action.choices))
    )
    def get_actions(self, application: Application) -> list[str]:
        return [str(action) for action in available_actions(application)]


ARCHIVE_NEEDS_REASON = "Say why you are archiving this candidate."
ENDS_BEFORE_START = "The interview must end after it starts."


class ArchiveSerializer(serializers.Serializer[object]):
    reason = serializers.CharField(
        max_length=REASON_MAX_LENGTH,
        error_messages={"blank": ARCHIVE_NEEDS_REASON, "required": ARCHIVE_NEEDS_REASON},
    )


class InterviewCreateSerializer(serializers.Serializer[object]):
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    format = serializers.ChoiceField(choices=InterviewFormat.choices)
    location = serializers.CharField(
        max_length=LOCATION_MAX_LENGTH, allow_blank=True, required=False, default=""
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["ends_at"] <= attrs["starts_at"]:
            raise serializers.ValidationError({"ends_at": [ENDS_BEFORE_START]})
        return attrs


PICK_A_TEAMMATE = "Pick a teammate who can see this job."
DECLINE_NEEDS_REASON = "Say why you are declining."
ONLY_APPROVER = "Only {name} can decide on this request."


class TeammateSerializer(serializers.Serializer[object]):
    id = serializers.UUIDField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    role = serializers.ChoiceField(
        source="membership.role", choices=CompanyRole.choices, read_only=True
    )


class ApprovalCreateSerializer(serializers.Serializer[object]):
    approver_id = serializers.UUIDField()
    note = serializers.CharField(
        max_length=REASON_MAX_LENGTH, allow_blank=True, required=False, default=""
    )


class ApprovalDecisionSerializer(serializers.Serializer[object]):
    approve = serializers.BooleanField()
    note = serializers.CharField(
        max_length=REASON_MAX_LENGTH, allow_blank=True, required=False, default=""
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs["approve"] and not attrs["note"]:
            raise serializers.ValidationError({"note": [DECLINE_NEEDS_REASON]})
        return attrs


class WaitingApplicationSerializer(serializers.ModelSerializer[Application]):
    job = DetailJobSerializer(read_only=True)
    candidate_name = serializers.CharField(source="candidate.full_name", read_only=True)

    class Meta:
        model = Application
        fields = ("id", "job", "candidate_name", "stage")
        read_only_fields = fields


class WaitingApprovalSerializer(serializers.ModelSerializer[ApprovalRequest]):
    application = WaitingApplicationSerializer(read_only=True)
    requested_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalRequest
        fields = ("id", "application", "requested_by_name", "note", "created_at")
        read_only_fields = fields

    def get_requested_by_name(self, approval: ApprovalRequest) -> str:
        return requester_name(approval)


EMPTY_NOTE = "Write something before you save the note."
NOT_YOUR_NOTE = "You can only delete your own notes."


class CandidateNoteSerializer(serializers.ModelSerializer[CandidateNote]):
    author_name = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = CandidateNote
        fields = ("id", "body", "author_name", "is_mine", "created_at")
        read_only_fields = fields

    def get_author_name(self, note: CandidateNote) -> str:
        return note.author.full_name if note.author else "A former teammate"

    def get_is_mine(self, note: CandidateNote) -> bool:
        return bool(note.author_id == self.context["request"].user.pk)


class NoteCreateSerializer(serializers.Serializer[object]):
    body = serializers.CharField(
        max_length=NOTE_MAX_LENGTH,
        error_messages={"blank": EMPTY_NOTE, "required": EMPTY_NOTE},
    )


class FeedbackSerializer(serializers.ModelSerializer[Feedback]):
    interview_id = serializers.UUIDField(source="interview.id", read_only=True)
    round = serializers.IntegerField(source="interview.round", read_only=True)
    author_name = serializers.CharField(source="author.full_name", read_only=True)
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = Feedback
        fields = (
            "id",
            "interview_id",
            "round",
            "verdict",
            "body",
            "author_name",
            "is_mine",
            "updated_at",
        )
        read_only_fields = fields

    def get_is_mine(self, feedback: Feedback) -> bool:
        return bool(feedback.author_id == self.context["request"].user.pk)


class FeedbackWriteSerializer(serializers.Serializer[object]):
    verdict = serializers.ChoiceField(choices=Verdict.choices)
    body = serializers.CharField(
        max_length=NOTE_MAX_LENGTH, allow_blank=True, required=False, default=""
    )


EMPTY_DISCUSSION = "Type a message."


class DiscussionMessageCreateSerializer(serializers.Serializer[object]):
    body = serializers.CharField(
        max_length=BODY_MAX_LENGTH,
        error_messages={"blank": EMPTY_DISCUSSION, "required": EMPTY_DISCUSSION},
    )

    def validate_body(self, value: str) -> str:
        cleaned = sanitise_body(value)
        if not cleaned:
            raise serializers.ValidationError(EMPTY_DISCUSSION)
        return cleaned


class CvTextSerializer(serializers.Serializer[object]):
    text = serializers.CharField(read_only=True, allow_blank=True)


class SubmissionCandidateFactsSerializer(CandidateFactsSerializer):
    class Meta(CandidateFactsSerializer.Meta):
        fields = ("id", *CandidateFactsSerializer.Meta.fields)
        read_only_fields = fields


class SubmissionDetailSerializer(RecruiterSubmissionDetailSerializer):
    """The recruiter's own submission: progress and the archive reason, never company notes,
    feedback or approvals."""

    # Widens the parent's id/name/title candidate to the full CV facts.
    candidate = SubmissionCandidateFactsSerializer(read_only=True)  # type: ignore[assignment]
    cv_kind = serializers.SerializerMethodField()
    interviews = InterviewSerializer(many=True, read_only=True)
    history = serializers.SerializerMethodField()

    class Meta(RecruiterSubmissionDetailSerializer.Meta):
        fields = (
            *RecruiterSubmissionDetailSerializer.Meta.fields,
            "rejected_reason",
            "cv_kind",
            "interviews",
            "history",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=CvKind.choices))
    def get_cv_kind(self, application: Application) -> str:
        return cv_kind_of(application)

    @extend_schema_field(StageEntrySerializer(many=True))
    def get_history(self, application: Application) -> Any:
        return history_of(application)
