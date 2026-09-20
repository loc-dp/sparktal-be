import uuid

from django.db import models
from django.utils import timezone
from phonenumber_field.modelfields import PhoneNumberField
from simple_history.models import HistoricalRecords

from apps.jobs.models import Job
from apps.recruiters.models import RecruiterProfile


class Stage(models.TextChoices):
    SHORTLISTED = "shortlisted", "Shortlisted"
    NEW = "new", "New applicant"
    SCREEN = "screen", "Screen"
    INTERVIEW = "interview", "Interview"
    OFFER = "offer", "Offer"
    ONBOARDED = "onboarded", "Onboarded"
    REJECTED = "rejected", "Rejected"


class Candidate(models.Model):
    """Owned by the recruiter who sourced them (their talent pool comes later)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(RecruiterProfile, on_delete=models.CASCADE, related_name="candidates")
    full_name = models.CharField(max_length=120)
    current_title = models.CharField(max_length=160, blank=True)
    email = models.EmailField(blank=True)
    phone = PhoneNumberField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("full_name",)

    def __str__(self) -> str:
        return self.full_name


class Application(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="applications")
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="applications")
    recruiter = models.ForeignKey(
        RecruiterProfile, on_delete=models.PROTECT, related_name="applications"
    )
    stage = models.CharField(max_length=16, choices=Stage.choices, default=Stage.NEW)
    cv_file = models.FileField(upload_to="cvs/%Y/%m/")
    cv_filename = models.CharField(max_length=255)
    submitted_at = models.DateTimeField(default=timezone.now)
    stage_changed_at = models.DateTimeField(default=timezone.now)
    rejected_reason = models.TextField(blank=True)
    is_duplicate = models.BooleanField(default=False)

    history = HistoricalRecords()

    class Meta:
        ordering = ("-submitted_at",)
        constraints = (
            models.UniqueConstraint(fields=("job", "candidate"), name="unique_application"),
        )

    def __str__(self) -> str:
        return f"{self.candidate.full_name} → {self.job.title}"

    def move_to(self, stage: str) -> None:
        self.stage = stage
        self.stage_changed_at = timezone.now()
        self.save(update_fields=["stage", "stage_changed_at"])

    def reject(self, reason: str) -> None:
        self.stage = Stage.REJECTED
        self.rejected_reason = reason
        self.stage_changed_at = timezone.now()
        self.save(update_fields=["stage", "rejected_reason", "stage_changed_at"])
