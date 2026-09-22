import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from apps.accounts.models import User
from apps.exports.constants import EXPORT_TTL
from apps.jobs.models import Job


class CvExportStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"


class CvExport(models.Model):
    """One asynchronous request to package CV files for a job."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="cv_exports")
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="cv_exports")
    application_ids = ArrayField(
        models.UUIDField(),
        default=list,
        blank=True,
        help_text="Empty means every application of the job that is not rejected.",
    )
    status = models.CharField(
        max_length=8, choices=CvExportStatus.choices, default=CvExportStatus.QUEUED
    )
    file = models.FileField(upload_to="cv-exports/%Y/%m/", null=True, blank=True)
    file_count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    downloaded_at = models.DateTimeField(null=True, blank=True)
    downloaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = (models.Index(fields=["status", "expires_at"]),)

    def __str__(self) -> str:
        return f"CV export {self.pk} · {self.job.title} · {self.status}"

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and timezone.now() >= self.expires_at

    def mark_running(self) -> None:
        self.status = CvExportStatus.RUNNING
        self.save(update_fields=["status"])

    def mark_done(self, *, file_count: int) -> None:
        now = timezone.now()
        self.status = CvExportStatus.DONE
        self.file_count = file_count
        self.finished_at = now
        self.expires_at = now + EXPORT_TTL
        self.save(update_fields=["status", "file_count", "finished_at", "expires_at", "file"])

    def mark_failed(self, error: str) -> None:
        self.status = CvExportStatus.FAILED
        self.error = error
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "error", "finished_at"])
