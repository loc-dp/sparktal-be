import uuid
from typing import Any, ClassVar

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from django.db.models import Index, Q, Value
from django.utils import timezone
from phonenumber_field.modelfields import PhoneNumberField
from simple_history.models import HistoricalRecords

from apps.candidates.duplicates import identity_hashes
from apps.candidates.parsing import MAX_YEARS
from apps.companies.models import Location
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


SEARCH_CONFIG = "simple"


class Candidate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(RecruiterProfile, on_delete=models.CASCADE, related_name="candidates")
    full_name = models.CharField(max_length=120)
    current_title = models.CharField(max_length=160, blank=True)
    email = models.EmailField(blank=True)
    phone = PhoneNumberField(blank=True)
    dob = models.DateField(null=True, blank=True)
    location = models.CharField(max_length=8, choices=Location.choices, blank=True)
    is_expat = models.BooleanField(default=False)
    languages = ArrayField(models.CharField(max_length=24), default=list, blank=True)
    skills = ArrayField(models.CharField(max_length=40), default=list, blank=True)
    years_experience = models.PositiveSmallIntegerField(null=True, blank=True)
    current_salary = models.DecimalField(max_digits=15, decimal_places=0, null=True, blank=True)
    expected_salary = models.DecimalField(max_digits=15, decimal_places=0, null=True, blank=True)
    cv_file = models.FileField(upload_to="pool-cvs/%Y/%m/", null=True, blank=True)
    cv_filename = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    search_vector = SearchVectorField(null=True)
    email_hash = models.CharField(max_length=64, blank=True, db_index=True)
    phone_hash = models.CharField(max_length=64, blank=True, db_index=True)
    name_dob_hash = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        ordering = ("full_name",)
        indexes: ClassVar[list[Index] | tuple[Index, ...]] = [
            GinIndex(fields=["search_vector"], name="candidate_search_gin")
        ]
        # The serializer says why; these stop admin, shell and imports from storing nonsense.
        constraints = (
            models.CheckConstraint(
                condition=Q(years_experience__lte=MAX_YEARS), name="candidate_years_in_range"
            ),
            models.CheckConstraint(
                condition=Q(current_salary__gte=0), name="candidate_current_salary_not_negative"
            ),
            models.CheckConstraint(
                condition=Q(expected_salary__gte=0), name="candidate_expected_salary_not_negative"
            ),
        )

    def __str__(self) -> str:
        return self.full_name

    def save(self, *args: Any, **kwargs: Any) -> None:
        for column, digest in identity_hashes(self).items():
            setattr(self, column, digest)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = {*update_fields, "email_hash", "phone_hash", "name_dob_hash"}
        super().save(*args, **kwargs)
        Candidate.objects.filter(pk=self.pk).update(
            search_vector=SearchVector(Value(self.searchable_text()), config=SEARCH_CONFIG)
        )

    def searchable_text(self) -> str:
        return " ".join(
            part
            for part in (self.full_name, self.current_title, " ".join(self.skills), self.notes)
            if part
        )


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
    duplicate_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="duplicates"
    )
    reason_for_leaving = models.TextField(blank=True)
    motivation = models.TextField(blank=True)
    other_notes = models.TextField(blank=True)
    consultant_note = models.TextField(blank=True)

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


class CvParseStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


class CvParseReader(models.TextChoices):
    CLAUDE = "claude", "Claude"
    RULES = "rules", "Rules"


LLM_USAGE_FIELDS = ("llm_model", "input_tokens", "output_tokens")


class CvParse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(RecruiterProfile, on_delete=models.CASCADE, related_name="cv_parses")
    file = models.FileField(upload_to="cv-parses/%Y/%m/")
    filename = models.CharField(max_length=255)
    status = models.CharField(
        max_length=8, choices=CvParseStatus.choices, default=CvParseStatus.QUEUED
    )
    fields = models.JSONField(default=dict)
    error = models.TextField(blank=True)
    # Which reader produced `fields`; blank while running or when nothing could read the file.
    reader = models.CharField(max_length=8, choices=CvParseReader.choices, blank=True)
    # Claude's cost is kept even when its reply was unusable and the rules took over.
    llm_model = models.CharField(max_length=64, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"CV parse {self.pk} · {self.filename} · {self.status}"

    def mark_running(self) -> None:
        self.status = CvParseStatus.RUNNING
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at"])

    def record_llm_usage(self, model: str, input_tokens: int, output_tokens: int) -> None:
        """Set Claude's cost; the next mark_done or mark_failed saves it."""
        self.llm_model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def mark_done(self, fields: dict[str, Any], reader: str) -> None:
        self.status = CvParseStatus.DONE
        self.fields = fields
        self.reader = reader
        self.error = ""
        self.finished_at = timezone.now()
        self.save(
            update_fields=["status", "fields", "reader", "error", "finished_at", *LLM_USAGE_FIELDS]
        )

    def mark_failed(self, error: str) -> None:
        self.status = CvParseStatus.FAILED
        self.error = error
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "error", "finished_at", *LLM_USAGE_FIELDS])
