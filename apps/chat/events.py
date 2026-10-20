import json
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from rest_framework.renderers import JSONRenderer

from apps.accounts.models import User
from apps.chat.access import channel_audience
from apps.chat.models import Message


def group_name(user: User) -> str:
    return f"user_{user.pk.hex}"


def message_event(message: Message) -> dict[str, Any]:
    from apps.chat.serializers import MessageSerializer

    serialized = json.loads(JSONRenderer().render(MessageSerializer(message).data))
    return {
        "type": "chat.message",
        "channel_id": str(message.channel_id),
        "message": serialized,
    }


def broadcast_message(message: Message) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    payload = message_event(message)
    for user in channel_audience(message.channel):
        async_to_sync(layer.group_send)(
            group_name(user), {"type": "event.push", "payload": payload}
        )


def close_sockets(user: User) -> None:
    """Close every events socket `user` has open, as when it connects signed out (4401).

    A socket checks who is signed in only when it connects, so a locked account would keep one
    open until it dropped. Call it after the lock is committed: a reconnect is then refused.
    """
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(group_name(user), {"type": "session.end"})
