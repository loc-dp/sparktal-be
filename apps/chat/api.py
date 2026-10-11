from typing import Any, cast

from django.db import transaction
from django.db.models import QuerySet
from django.http import FileResponse, Http404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.chat.access import can_access_channel, channels_visible_to, with_unread_count
from apps.chat.events import broadcast_message
from apps.chat.models import Channel, ChannelRead, Message
from apps.chat.serializers import (
    ChannelSerializer,
    MessageCreateSerializer,
    MessagePageSerializer,
    MessageSerializer,
    ReadResultSerializer,
    ReadSerializer,
)
from apps.core.http import content_disposition

MAX_PAGE = 50
DEFAULT_PAGE = 30
CHANNEL_QS_HINTS = ("job", "job__company", "recruiter")
MESSAGE_QS_HINTS = ("author", "author__recruiter", "application", "application__candidate")


def get_channel_or_404(request: Request, pk: Any) -> Channel:
    channel = Channel.objects.filter(pk=pk).select_related(*CHANNEL_QS_HINTS).first()
    if channel is None or not can_access_channel(cast(User, request.user), channel):
        raise Http404
    return channel


MESSAGE_PAGE_PARAMETERS = [
    OpenApiParameter("before", OpenApiTypes.DATETIME, description="Return older messages."),
    OpenApiParameter(
        "limit",
        OpenApiTypes.INT,
        description=f"1–{MAX_PAGE}, default {DEFAULT_PAGE}.",
    ),
]


def page_limit(request: Request) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return DEFAULT_PAGE
    try:
        value = int(raw)
    except ValueError as error:
        raise serializers.ValidationError({"limit": ["Enter a whole number."]}) from error
    if not 1 <= value <= MAX_PAGE:
        raise serializers.ValidationError({"limit": [f"limit must be between 1 and {MAX_PAGE}."]})
    return value


def message_page(request: Request, queryset: QuerySet[Message]) -> dict[str, Any]:
    """One window of messages, oldest first, and whether older ones exist."""
    limit = page_limit(request)
    queryset = queryset.select_related(*MESSAGE_QS_HINTS)
    before = request.query_params.get("before")
    if before:
        parsed = serializers.DateTimeField().to_internal_value(before)
        queryset = queryset.filter(created_at__lt=parsed)
    window = list(queryset.order_by("-created_at", "-id")[: limit + 1])
    page = list(reversed(window[:limit]))
    return {
        "results": MessageSerializer(page, many=True, context={"request": request}).data,
        "has_more": len(window) > limit,
    }


@extend_schema_view(
    get=extend_schema(
        tags=["chat"],
        operation_id="chat_channels_list",
        responses=ChannelSerializer(many=True),
    )
)
class ChannelListView(APIView):
    def get(self, request: Request) -> Response:
        user = cast(User, request.user)
        queryset = with_unread_count(channels_visible_to(user), user).select_related(
            *CHANNEL_QS_HINTS
        )
        return Response(ChannelSerializer(queryset, many=True, context={"request": request}).data)


@extend_schema_view(
    get=extend_schema(
        tags=["chat"],
        operation_id="chat_channels_messages_list",
        parameters=MESSAGE_PAGE_PARAMETERS,
        responses=MessagePageSerializer,
    ),
    post=extend_schema(
        tags=["chat"],
        operation_id="chat_channels_messages_create",
        request={"multipart/form-data": MessageCreateSerializer},
        responses={201: MessageSerializer},
    ),
)
class ChannelMessagesView(APIView):
    def get(self, request: Request, pk: Any) -> Response:
        channel = get_channel_or_404(request, pk)
        return Response(message_page(request, channel.messages.all()))

    def post(self, request: Request, pk: Any) -> Response:
        channel = get_channel_or_404(request, pk)
        serializer = MessageCreateSerializer(
            data=request.data, context={"request": request, "channel": channel}
        )
        serializer.is_valid(raise_exception=True)
        message = serializer.save()
        transaction.on_commit(lambda: broadcast_message(message))
        return Response(
            MessageSerializer(message, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    post=extend_schema(
        tags=["chat"],
        operation_id="chat_channels_read_create",
        request=ReadSerializer,
        responses=ReadResultSerializer,
    )
)
class ChannelReadView(APIView):
    def post(self, request: Request, pk: Any) -> Response:
        channel = get_channel_or_404(request, pk)
        serializer = ReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        moment = serializer.validated_data.get("last_read_at") or timezone.now()
        user = cast(User, request.user)
        marker, created = ChannelRead.objects.get_or_create(
            channel=channel, user=user, defaults={"last_read_at": moment}
        )
        if not created and moment > marker.last_read_at:
            marker.last_read_at = moment
            marker.save(update_fields=["last_read_at"])
        fresh = with_unread_count(Channel.objects.filter(pk=channel.pk), user).get()
        unread_count = cast(Any, fresh).unread_count
        return Response({"last_read_at": marker.last_read_at, "unread_count": unread_count})


@extend_schema_view(
    get=extend_schema(
        tags=["chat"],
        operation_id="chat_messages_attachment_retrieve",
        responses={(200, "application/octet-stream"): OpenApiTypes.BINARY},
    )
)
class MessageAttachmentView(APIView):
    def get(self, request: Request, pk: Any) -> FileResponse:
        message = (
            Message.objects.filter(pk=pk)
            .select_related("channel", "channel__job", "channel__recruiter")
            .first()
        )
        if message is None or not message.attachment:
            raise Http404
        if not can_access_channel(cast(User, request.user), message.channel):
            raise Http404
        try:
            handle = message.attachment.open("rb")
        except (FileNotFoundError, OSError) as error:
            raise Http404 from error
        response = FileResponse(handle, content_type="application/octet-stream")
        response["Content-Disposition"] = content_disposition(
            message.attachment_name or "attachment"
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response
