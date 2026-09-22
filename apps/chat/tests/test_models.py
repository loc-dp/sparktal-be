import pytest
from django.db.utils import IntegrityError

from apps.chat.models import Channel, MessageKind
from apps.chat.tests.factories import ChannelFactory, MessageFactory
from apps.jobs.models import Job
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db


def test_a_job_and_recruiter_pair_is_unique(job: Job, recruiter_profile: RecruiterProfile) -> None:
    Channel.objects.create(job=job, recruiter=recruiter_profile)
    with pytest.raises(IntegrityError):
        Channel.objects.create(job=job, recruiter=recruiter_profile)


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
