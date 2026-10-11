"""Messages about one candidate, posted into the (job, recruiter) chat channel."""

from django.db import transaction

from apps.accounts.models import User
from apps.candidates.models import Application
from apps.chat.events import broadcast_message
from apps.chat.models import Message, MessageKind
from apps.chat.signals import ensure_channel


def post_message(
    application: Application,
    *,
    author: User | None,
    body: str,
    kind: str = MessageKind.USER,
) -> Message:
    channel, _ = ensure_channel(application.job, application.recruiter)
    message = Message.objects.create(
        channel=channel, application=application, author=author, kind=kind, body=body
    )
    transaction.on_commit(lambda: broadcast_message(message))
    return message


def post_system_message(application: Application, body: str) -> Message:
    return post_message(application, author=None, body=body, kind=MessageKind.SYSTEM)
