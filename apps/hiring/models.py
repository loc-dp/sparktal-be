"""What a company does with one application: interview rounds, feedback, notes, approvals."""

import uuid

from django.db import models
from django.db.models import F, Q

from apps.accounts.models import User
from apps.candidates.models import Application, Stage

LOCATION_MAX_LENGTH = 500
NOTE_MAX_LENGTH = 4000
REASON_MAX_LENGTH = 1000


class InterviewFormat(models.TextChoices):
    IN_PERSON = "in_person", "In person"
    ONLINE = "online", "Online"


class Verdict(models.TextChoices):
    STRONG_YES = "strong_yes", "Strong yes"
    YES = "yes", "Yes"
    NO = "no", "No"
    STRONG_NO = "strong_no", "Strong no"


class ApprovalStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    DECLINED = "declined", "Declined"
    CANCELLED = "cancelled", "Cancelled"


class Interview(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="interviews"
    )
    round = models.PositiveSmallIntegerField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    format = models.CharField(max_length=16, choices=InterviewFormat.choices)
    # A street address for in-person rounds, a meeting link for online ones.
    location = models.CharField(max_length=LOCATION_MAX_LENGTH, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("round",)
        constraints = (
            models.UniqueConstraint(fields=("application", "round"), name="hiring_interview_round"),
            models.CheckConstraint(
                condition=Q(ends_at__gt=F("starts_at")), name="hiring_interview_ends_after_start"
            ),
            models.CheckConstraint(condition=Q(round__gte=1), name="hiring_interview_round_from_1"),
        )

    def __str__(self) -> str:
        return f"Round {self.round} · {self.application_id}"


class Feedback(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    interview = models.ForeignKey(Interview, on_delete=models.CASCADE, related_name="feedback")
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    verdict = models.CharField(max_length=16, choices=Verdict.choices)
    body = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("interview", "author"), name="hiring_feedback_one_per_author"
            ),
        )

    def __str__(self) -> str:
        return f"{self.verdict} · round {self.interview.round}"


class CandidateNote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="company_notes"
    )
    author = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return self.body[:40]


class ApprovalRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="approval_requests"
    )
    requested_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    approver = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="approval_requests_to_decide"
    )
    # The stage the application was in when someone asked; approving moves it one step on.
    stage = models.CharField(max_length=16, choices=Stage.choices)
    status = models.CharField(
        max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING
    )
    note = models.TextField(blank=True)
    decision_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("application",),
                condition=Q(status="pending"),
                name="hiring_one_pending_approval",
            ),
        )

    def __str__(self) -> str:
        return f"{self.status} · {self.application_id}"
