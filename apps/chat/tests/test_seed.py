import pytest
from django.core.management import call_command

from apps.chat.models import Channel, Message, MessageKind

pytestmark = pytest.mark.django_db


BACKEND = "Senior Backend Engineer"


def test_seed_opens_backend_threads_and_a_realistic_conversation() -> None:
    call_command("seed_demo", force=True)

    general = Channel.objects.filter(job__title=BACKEND, application__isnull=True)
    assert {"NorthStar Talent", "Bluefin Search"} <= set(
        general.values_list("recruiter__nickname", flat=True)
    )
    quan = Channel.objects.get(
        job__title=BACKEND,
        recruiter__nickname="NorthStar Talent",
        application__candidate__full_name="Phạm Minh Quân",
    )
    messages = list(quan.messages.all())
    assert len(messages) >= 4
    assert any(message.kind == MessageKind.SYSTEM for message in messages)
    assert any(message.quote_source for message in messages)
    assert quan.last_message_at == messages[-1].created_at
    assert {message.author.kind for message in messages if message.author} == {
        "company",
        "recruiter",
    }
    gia_bao = Channel.objects.get(
        job__title=BACKEND, application__candidate__full_name="Mai Gia Bảo"
    )
    assert any(message.attachment_name.endswith(".pdf") for message in gia_bao.messages.all())


def test_a_message_about_a_candidate_lives_in_that_candidates_thread() -> None:
    call_command("seed_demo", force=True)

    for channel in Channel.objects.all():
        for message in channel.messages.all():
            assert message.application_id == channel.application_id, (channel, message.body)
    assert not any(
        "Phạm Minh Quân" in message.body
        for message in Message.objects.filter(channel__application__isnull=True)
    )


def test_a_duplicate_flag_line_sits_in_the_thread_of_a_real_duplicate() -> None:
    call_command("seed_demo", force=True)

    flagged = Message.objects.filter(body__contains="flagged as a duplicate")
    assert flagged.exists()
    for message in flagged:
        assert message.application is not None, message.body
        assert message.application.duplicate_of_id is not None, message.body
        assert message.channel.application_id == message.application_id
        assert message.application.candidate.full_name in message.body


def test_each_demo_headhunter_keeps_a_conversation_in_a_general_thread() -> None:
    call_command("seed_demo", force=True)

    for nickname in ("NorthStar Talent", "Bluefin Search", "Atlas Hunters"):
        talked = Message.objects.filter(
            channel__application__isnull=True,
            channel__recruiter__nickname=nickname,
            kind=MessageKind.USER,
        )
        assert talked.filter(author__kind="recruiter").exists(), nickname
        assert talked.filter(author__kind="company").exists(), nickname


def test_seed_totals_are_pinned() -> None:
    """Two runs, same counts. Raise the numbers when the demo story grows on purpose."""
    call_command("seed_demo", reset=True, force=True)
    first = (
        Channel.objects.filter(application__isnull=True).count(),
        Channel.objects.filter(application__isnull=False).count(),
        Message.objects.count(),
    )
    call_command("seed_demo", reset=True, force=True)
    again = (
        Channel.objects.filter(application__isnull=True).count(),
        Channel.objects.filter(application__isnull=False).count(),
        Message.objects.count(),
    )

    assert first == again == (11, 18, 45)  # general threads, candidate threads, messages


def test_every_channel_has_its_last_message_time() -> None:
    call_command("seed_demo", force=True)

    for channel in Channel.objects.all():
        newest = channel.messages.order_by("-created_at", "-id").first()
        assert channel.last_message_at == (newest.created_at if newest else None), channel


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


def test_system_lines_in_other_channels_carry_the_real_dates() -> None:
    from apps.candidates.models import Application

    call_command("seed_demo", force=True)
    qa = Application.objects.get(job__title="QA Engineer")
    line = Message.objects.get(application=qa, kind="system")
    assert line.created_at == qa.submitted_at
