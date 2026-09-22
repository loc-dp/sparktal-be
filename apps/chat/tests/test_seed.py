import pytest
from django.core.management import call_command

from apps.chat.models import Channel, Message, MessageKind

pytestmark = pytest.mark.django_db


def test_seed_opens_backend_channels_and_a_realistic_conversation() -> None:
    call_command("seed_demo", force=True)

    channels = Channel.objects.filter(job__title="Senior Backend Engineer")
    assert {"NorthStar Talent", "Bluefin Search"} <= set(
        channels.values_list("recruiter__nickname", flat=True)
    )
    channel = channels.get(recruiter__nickname="NorthStar Talent")
    messages = list(channel.messages.all())
    assert len(messages) >= 6
    assert any(message.kind == MessageKind.SYSTEM for message in messages)
    assert any(message.quote_source for message in messages)
    assert any(message.attachment_name.endswith(".pdf") for message in messages)
    assert channel.last_message_at == messages[-1].created_at
    assert {message.author.kind for message in messages if message.author} == {
        "company",
        "recruiter",
    }


def test_seed_is_idempotent() -> None:
    call_command("seed_demo", force=True)
    before = Message.objects.count()
    call_command("seed_demo", force=True)
    assert Message.objects.count() == before


def test_reset_does_not_duplicate_system_messages() -> None:
    call_command("seed_demo", reset=True, force=True)
    before = Message.objects.count()
    call_command("seed_demo", reset=True, force=True)
    assert Message.objects.count() == before
