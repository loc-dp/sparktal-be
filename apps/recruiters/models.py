import uuid
from typing import ClassVar

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from phonenumber_field.modelfields import PhoneNumberField

from apps.accounts.models import User


class RecruiterStatus(models.TextChoices):
    PENDING = "pending", "Pending review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class RecruiterProfile(models.Model):
    """Companies only ever see the nickname (PRODUCT.md §3.2)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="recruiter")
    nickname = models.CharField(max_length=60, unique=True)
    status = models.CharField(
        max_length=10, choices=RecruiterStatus.choices, default=RecruiterStatus.PENDING
    )
    phone = PhoneNumberField(blank=True)
    expertise = ArrayField(models.CharField(max_length=24), default=list, blank=True)
    years_experience = models.PositiveSmallIntegerField(null=True, blank=True)
    linkedin = models.URLField(blank=True)
    bio = models.TextField(blank=True)
    agreed_terms_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    review_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("nickname",)
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(Lower("nickname"), name="recruiter_nickname_ci")
        ]

    def __str__(self) -> str:
        return self.nickname

    def approve(self, by: User) -> None:
        self.status = RecruiterStatus.APPROVED
        self.reviewed_by = by
        self.reviewed_at = timezone.now()
        self.review_note = ""
        self.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])

    def reject(self, by: User, note: str) -> None:
        self.status = RecruiterStatus.REJECTED
        self.reviewed_by = by
        self.reviewed_at = timezone.now()
        self.review_note = note
        self.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])
