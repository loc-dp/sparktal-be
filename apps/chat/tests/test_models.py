from typing import Any, cast

import pytest
from django.db.utils import IntegrityError

from apps.candidates.models import Application
from apps.candidates.tests.factories import ApplicationFactory
from apps.chat.models import Channel, Message, MessageKind
from apps.chat.tests.factories import ChannelFactory, MessageFactory
from apps.jobs.models import Job
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db


# Threads are fetched with `get_or_create`, not created: submitting a candidate may already
# have opened them through the chat signals.


@pytest.fixture
def application(job: Job, recruiter_profile: RecruiterProfile) -> Application:
    return cast(
        Application,
        ApplicationFactory(
            job=job, recruiter=recruiter_profile, candidate__owner=recruiter_profile
        ),
    )


@pytest.fixture
def general_channel(application: Application) -> Channel:
    channel, _ = Channel.objects.get_or_create(
        job=application.job, recruiter=application.recruiter, application=None
    )
    return channel


@pytest.fixture
def candidate_channel(application: Application) -> Channel:
    channel, _ = Channel.objects.get_or_create(
        job=application.job, recruiter=application.recruiter, application=application
    )
    return channel


@pytest.fixture
def application_with_thread_and_messages(
    application: Application, general_channel: Channel, candidate_channel: Channel
) -> dict[str, Any]:
    return {
        "application": application,
        "general": general_channel,
        "thread": candidate_channel,
        "general_messages": {
            MessageFactory(channel=general_channel, body="About the job").pk,
            # A general message that names the candidate keeps its row when they go.
            MessageFactory(channel=general_channel, application=application, body="FYI").pk,
        },
        "thread_messages": {
            MessageFactory(channel=candidate_channel, application=application, body="CV?").pk
        },
    }


def test_one_general_channel_per_job_and_recruiter(
    job: Job, recruiter_profile: RecruiterProfile
) -> None:
    Channel.objects.get_or_create(job=job, recruiter=recruiter_profile, application=None)
    with pytest.raises(IntegrityError):
        Channel.objects.create(job=job, recruiter=recruiter_profile)


def test_one_thread_per_application(application: Application) -> None:
    Channel.objects.get_or_create(
        job=application.job, recruiter=application.recruiter, application=application
    )
    with pytest.raises(IntegrityError):
        Channel.objects.create(
            job=application.job, recruiter=application.recruiter, application=application
        )


def test_general_and_candidate_channels_coexist(application: Application) -> None:
    pair = {"job": application.job, "recruiter": application.recruiter}
    # Submitted before any thread exists: the submission signal looks the pair's channel up.
    other = ApplicationFactory(candidate__owner=application.recruiter, **pair)
    general, _ = Channel.objects.get_or_create(application=None, **pair)
    thread, _ = Channel.objects.get_or_create(application=application, **pair)
    other_thread, _ = Channel.objects.get_or_create(application=other, **pair)

    assert set(Channel.objects.filter(**pair)) == {general, thread, other_thread}
    assert (application.channel, other.channel) == (thread, other_thread)


def test_deleting_application_deletes_its_thread(
    application_with_thread_and_messages: dict[str, Any],
) -> None:
    made = application_with_thread_and_messages
    general_before = set(made["general"].messages.values_list("pk", flat=True))
    assert made["general_messages"] <= general_before

    made["application"].delete()

    assert not Channel.objects.filter(pk=made["thread"].pk).exists()
    assert not Message.objects.filter(pk__in=made["thread_messages"]).exists()
    assert set(made["general"].messages.values_list("pk", flat=True)) == general_before
    assert not made["general"].messages.filter(application__isnull=False).exists()


def test_kind(general_channel: Channel, candidate_channel: Channel) -> None:
    assert general_channel.kind == "general"
    assert candidate_channel.kind == "candidate"


def test_a_thread_names_its_candidate(
    general_channel: Channel, candidate_channel: Channel, application: Application
) -> None:
    assert str(general_channel) == "NorthStar Talent · Senior Backend Engineer"
    assert str(candidate_channel) == (
        f"NorthStar Talent · Senior Backend Engineer · {application.candidate.full_name}"
    )


def test_sending_a_message_moves_last_message_at(channel: Channel) -> None:
    assert channel.last_message_at is None
    message = MessageFactory(channel=channel, body="Hello")
    channel.refresh_from_db()
    assert channel.last_message_at == message.created_at


def test_system_messages_have_no_author(channel: Channel) -> None:
    message = MessageFactory(channel=channel, author=None, kind=MessageKind.SYSTEM, body="x")
    assert message.author is None


def test_messages_are_ordered_oldest_first(channel: Channel) -> None:
    first = MessageFactory(channel=channel, body="one")
    second = MessageFactory(channel=channel, body="two")
    assert list(channel.messages.all()) == [first, second]


def test_channels_are_ordered_by_last_activity(job: Job) -> None:
    quiet = ChannelFactory(job=job)
    busy = ChannelFactory(job=job)
    MessageFactory(channel=busy, body="ping")
    assert list(Channel.objects.all()) == [busy, quiet]
