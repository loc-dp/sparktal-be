import uuid

from django.db import models

from apps.accounts.models import User


class RecruiterProfile(models.Model):
    """Companies only ever see the nickname (PRODUCT.md §3.2)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="recruiter")
    nickname = models.CharField(max_length=60, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("nickname",)

    def __str__(self) -> str:
        return self.nickname
