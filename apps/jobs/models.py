import secrets
import uuid
from typing import Any

from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from apps.accounts.models import User
from apps.companies.models import Company, Office

BOUNTY_PCT_MIN = 5
BOUNTY_PCT_MAX = 30


class JobStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACTIVE = "active", "Active"
    CLOSED = "closed", "Closed"


class JobFunction(models.TextChoices):
    FINANCE = "finance", "Finance"
    ENGINEERING = "engineering", "Engineering"
    DATA = "data", "Data"
    DESIGN = "design", "Design"
    LEGAL = "legal", "Legal"
    MARKETING = "marketing", "Marketing"
    SALES = "sales", "Sales"
    OPERATIONS = "operations", "Operations"
    OTHER = "other", "Other"


class PayBasis(models.TextChoices):
    GROSS = "gross", "Gross"
    NET = "net", "Net"


class WorkModel(models.TextChoices):
    ONSITE = "onsite", "On-site"
    HYBRID = "hybrid", "Hybrid"
    REMOTE = "remote", "Remote"


class WorkingHours(models.TextChoices):
    MON_FRI = "40h_mon_fri", "40h (Mon - Fri)"
    MON_SAT_HALF = "44h_mon_sat_half", "44h (Mon - Fri + half Sat)"
    MON_SAT = "48h_mon_sat", "48h (Mon - Sat)"


class RoleType(models.TextChoices):
    NEW_POSITION = "new_position", "New position"
    REPLACEMENT = "replacement", "Replacement"


class SearchDuration(models.TextChoices):
    JUST_CREATED = "just_created", "Just created"
    UNDER_2_WEEKS = "under_2_weeks", "Less than two weeks"
    UNDER_1_MONTH = "2_weeks_to_1_month", "Two weeks to a month"
    OVER_1_MONTH = "over_1_month", "More than a month"
    OVER_3_MONTHS = "over_3_months", "More than three months"


class Language(models.TextChoices):
    VIETNAMESE = "vi", "Vietnamese"
    ENGLISH = "en", "English"
    MANDARIN = "zh", "Mandarin"
    JAPANESE = "ja", "Japanese"
    OTHER = "other", "Other"


INTERVIEW_ROUNDS_MIN = 1
INTERVIEW_ROUNDS_MAX = 10


class BountyType(models.TextChoices):
    PCT = "pct", "% of annual salary"
    FIXED = "fixed", "Fixed amount"


def new_share_token() -> str:
    return secrets.token_urlsafe(16)


class Job(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="jobs")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="+")

    title = models.CharField(max_length=120)
    function = models.CharField(
        max_length=16, choices=JobFunction.choices, default=JobFunction.OTHER
    )
    why_we_need_you = models.TextField()
    roles = models.TextField(help_text="One responsibility per line.")
    qualifications = models.TextField(help_text="One must-have per line.")
    nice_to_have = models.TextField(blank=True)
    benefits = models.TextField(blank=True)

    # The rest of the SPARKTAL JOB BRIEF. Jobs posted before it leave these blank.
    languages = ArrayField(
        models.CharField(max_length=8, choices=Language.choices), default=list, blank=True
    )
    core_competencies = models.TextField(blank=True, help_text="One per line.")
    probation_expectations = models.TextField(blank=True)
    reporting_line = models.TextField(blank=True)
    role_type = models.CharField(max_length=16, choices=RoleType.choices, blank=True)
    has_internal_candidate = models.BooleanField(null=True, blank=True)
    search_duration = models.CharField(max_length=24, choices=SearchDuration.choices, blank=True)
    target_start_date = models.DateField(null=True, blank=True)
    interview_rounds_count = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[
            MinValueValidator(INTERVIEW_ROUNDS_MIN),
            MaxValueValidator(INTERVIEW_ROUNDS_MAX),
        ],
    )
    interview_process_details = models.TextField(blank=True)

    salary_min = models.DecimalField(max_digits=15, decimal_places=0, null=True, blank=True)
    salary_max = models.DecimalField(max_digits=15, decimal_places=0, null=True, blank=True)
    pay_basis = models.CharField(max_length=8, choices=PayBasis.choices, default=PayBasis.GROSS)
    salary_negotiable = models.BooleanField(null=True, blank=True)
    allowances = models.TextField(blank=True, help_text="One allowance or reward per line.")
    work_model = models.CharField(max_length=8, choices=WorkModel.choices, default=WorkModel.ONSITE)
    office = models.ForeignKey(
        Office, on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs"
    )
    working_hours = models.CharField(max_length=24, choices=WorkingHours.choices, blank=True)

    bounty_type = models.CharField(max_length=8, choices=BountyType.choices, default=BountyType.PCT)
    bounty_pct = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        default=15,
        validators=[MinValueValidator(BOUNTY_PCT_MIN), MaxValueValidator(BOUNTY_PCT_MAX)],
    )
    bounty_amount = models.DecimalField(max_digits=15, decimal_places=0, null=True, blank=True)

    skills = ArrayField(models.CharField(max_length=40), default=list, blank=True)
    status = models.CharField(max_length=8, choices=JobStatus.choices, default=JobStatus.PENDING)
    note_for_recruiters = models.TextField(blank=True)
    share_token = models.CharField(
        max_length=32, unique=True, default=new_share_token, editable=False
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords(excluded_fields=["updated_at"])

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.title} @ {self.company.name}"

    @property
    def location_label(self) -> str:
        if self.work_model == WorkModel.REMOTE:
            return "Remote (Vietnam)"
        return self.office.label if self.office else "—"

    def publish(self) -> None:
        self.status = JobStatus.ACTIVE
        self.published_at = self.published_at or timezone.now()
        self.closed_at = None
        self.save(update_fields=["status", "published_at", "closed_at", "updated_at"])

    def close(self) -> None:
        self.status = JobStatus.CLOSED
        self.closed_at = timezone.now()
        self.save(update_fields=["status", "closed_at", "updated_at"])

    def reopen(self) -> None:
        self.status = JobStatus.ACTIVE
        self.closed_at = None
        self.published_at = self.published_at or timezone.now()
        self.save(update_fields=["status", "closed_at", "published_at", "updated_at"])


class JobAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="assignments")
    recruiter = models.ForeignKey(
        "recruiters.RecruiterProfile", on_delete=models.CASCADE, related_name="assignments"
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-joined_at", "-id")
        constraints = (
            models.UniqueConstraint(fields=("job", "recruiter"), name="job_assignment_unique"),
        )

    def __str__(self) -> str:
        return f"{self.recruiter.nickname} · {self.job.title}"

    @property
    def is_active(self) -> bool:
        return self.left_at is None


class JdParseStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


JD_LLM_USAGE_FIELDS = ("llm_model", "input_tokens", "output_tokens")


class JdParse(models.Model):
    """One job description read by Claude to prefill Create new job; purged after a day."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="jd_parses")
    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="jd_parses")
    file = models.FileField(upload_to="jd-parses/%Y/%m/")
    filename = models.CharField(max_length=255)
    status = models.CharField(
        max_length=8, choices=JdParseStatus.choices, default=JdParseStatus.QUEUED
    )
    fields = models.JSONField(default=dict)
    error = models.TextField(blank=True)
    # Claude's cost is kept even when its reply was unusable.
    llm_model = models.CharField(max_length=64, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "JD parse"

    def __str__(self) -> str:
        return f"JD parse {self.pk} · {self.filename} · {self.status}"

    def mark_running(self) -> None:
        self.status = JdParseStatus.RUNNING
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at"])

    def record_llm_usage(self, model: str, input_tokens: int, output_tokens: int) -> None:
        """Set Claude's cost; the next mark_done or mark_failed saves it."""
        self.llm_model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def mark_done(self, fields: dict[str, Any]) -> None:
        self.status = JdParseStatus.DONE
        self.fields = fields
        self.error = ""
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "fields", "error", "finished_at", *JD_LLM_USAGE_FIELDS])

    def mark_failed(self, error: str) -> None:
        self.status = JdParseStatus.FAILED
        self.error = error
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "error", "finished_at", *JD_LLM_USAGE_FIELDS])
