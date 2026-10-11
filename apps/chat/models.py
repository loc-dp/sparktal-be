import uuid
from typing import ClassVar

from django.db import models
from django.db.models import F

from apps.accounts.models import User
from apps.jobs.models import Job
from apps.recruiters.models import RecruiterProfile

BODY_MAX_LENGTH = 4000


class MessageKind(models.TextChoices):
    USER = "user", "User"
    SYSTEM = "system", "System"


class Channel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="channels")
    recruiter = models.ForeignKey(
        RecruiterProfile, on_delete=models.CASCADE, related_name="channels"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=("job", "recruiter"), name="chat_channel_job_recruiter")
        ]
        ordering = (F("last_message_at").desc(nulls_last=True), "-created_at")

    def __str__(self) -> str:
        return f"{self.recruiter.nickname} · {self.job.title}"


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="messages")
    # Set when the message is about one candidate (the Discussion panel on candidate detail).
    application = models.ForeignKey(
        "candidates.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    author = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    kind = models.CharField(max_length=8, choices=MessageKind.choices, default=MessageKind.USER)
    body = models.TextField(blank=True)
    attachment = models.FileField(upload_to="chat/%Y/%m/", null=True, blank=True)
    attachment_name = models.CharField(max_length=255, blank=True)
    attachment_size = models.PositiveIntegerField(default=0)
    quote_source = models.CharField(max_length=120, blank=True)
    quote_text = models.CharField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["channel", "-created_at"], name="chat_msg_channel_recent")
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.body[:40]}"

    def save(self, *args: object, **kwargs: object) -> None:
        created = self._state.adding
        super().save(*args, **kwargs)  # type: ignore[arg-type]
        if created:
            Channel.objects.filter(pk=self.channel_id).update(last_message_at=self.created_at)


class ChannelRead(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="channel_reads")
    last_read_at = models.DateTimeField()

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=("channel", "user"), name="chat_read_channel_user")
        ]

    def __str__(self) -> str:
        return f"{self.user_id}@{self.channel_id}"
