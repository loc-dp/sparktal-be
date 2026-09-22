from typing import Any, ClassVar

import nh3
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.accounts.models import User, UserKind
from apps.chat.attachments import validate_attachment
from apps.chat.models import BODY_MAX_LENGTH, Channel, Message, MessageKind

EMPTY_MESSAGE = "Type a message or attach a file."


def display_name(user: User | None) -> str:
    if user is None:
        return "System"
    if user.kind == UserKind.RECRUITER:
        profile = getattr(user, "recruiter", None)
        return profile.nickname if profile is not None else "Recruiter"
    return user.full_name


def sanitise_body(value: str) -> str:
    return nh3.clean(value or "", tags=set(), attributes={}, strip_comments=True).strip()


class MessageSerializer(serializers.ModelSerializer[Message]):
    author_id = serializers.UUIDField(source="author.id", read_only=True, allow_null=True)
    author_name = serializers.SerializerMethodField()
    attachment_url = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = (
            "id",
            "channel",
            "kind",
            "author_id",
            "author_name",
            "body",
            "attachment_name",
            "attachment_size",
            "attachment_url",
            "quote_source",
            "quote_text",
            "created_at",
        )

    def get_author_name(self, message: Message) -> str:
        return display_name(message.author)

    def get_attachment_url(self, message: Message) -> str | None:
        if not message.attachment:
            return None
        return f"/api/v1/chat/messages/{message.pk}/attachment"


class MessageCreateSerializer(serializers.ModelSerializer[Message]):
    class Meta:
        model = Message
        fields = ("body", "attachment", "quote_source", "quote_text")
        extra_kwargs: ClassVar[dict[str, dict[str, bool]]] = {
            "body": {"required": False, "allow_blank": True}
        }

    def validate_body(self, value: str) -> str:
        if len(value) > BODY_MAX_LENGTH:
            raise serializers.ValidationError(
                f"A message can be at most {BODY_MAX_LENGTH} characters."
            )
        return sanitise_body(value)

    def validate_attachment(self, upload: Any) -> Any:
        if upload is None:
            return upload
        try:
            validate_attachment(upload)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from error
        return upload

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs.get("body") and not attrs.get("attachment"):
            raise serializers.ValidationError({"body": [EMPTY_MESSAGE]})
        return attrs

    def create(self, validated_data: dict[str, Any]) -> Message:
        upload = validated_data.get("attachment")
        return Message.objects.create(
            channel=self.context["channel"],
            author=self.context["request"].user,
            kind=MessageKind.USER,
            attachment_name=getattr(upload, "name", "") or "",
            attachment_size=getattr(upload, "size", 0) or 0,
            **validated_data,
        )


class ChannelSerializer(serializers.ModelSerializer[Channel]):
    job = serializers.SerializerMethodField()
    company = serializers.SerializerMethodField()
    recruiter = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Channel
        fields = (
            "id",
            "job",
            "company",
            "recruiter",
            "unread_count",
            "last_message_at",
            "last_message",
        )

    def get_job(self, channel: Channel) -> dict[str, Any]:
        return {
            "id": str(channel.job_id),
            "title": channel.job.title,
            "status": channel.job.status,
        }

    def get_company(self, channel: Channel) -> dict[str, Any]:
        return {
            "id": str(channel.job.company_id),
            "name": channel.job.company.name,
        }

    def get_recruiter(self, channel: Channel) -> dict[str, Any]:
        return {
            "id": str(channel.recruiter_id),
            "nickname": channel.recruiter.nickname,
        }

    def get_last_message(self, channel: Channel) -> dict[str, Any] | None:
        message = (
            channel.messages.select_related("author", "author__recruiter")
            .order_by("-created_at", "-id")
            .first()
        )
        if message is None:
            return None
        return {
            "id": str(message.pk),
            "kind": message.kind,
            "body": message.body,
            "author_id": str(message.author_id) if message.author_id else None,
            "author_name": display_name(message.author),
            "attachment_name": message.attachment_name,
            "created_at": message.created_at,
        }


class MessagePageSerializer(serializers.Serializer[Any]):
    results = MessageSerializer(many=True)
    has_more = serializers.BooleanField()


class ReadSerializer(serializers.Serializer[Any]):
    last_read_at = serializers.DateTimeField(required=False)


class ReadResultSerializer(serializers.Serializer[Any]):
    last_read_at = serializers.DateTimeField()
    unread_count = serializers.IntegerField()
