from datetime import datetime, timedelta
from importlib import import_module
from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.accounts.models import User
from apps.candidates.models import Application
from apps.candidates.tests.factories import ApplicationFactory
from apps.chat.access import with_unread_count
from apps.chat.models import Channel, ChannelRead, Message, MessageKind
from apps.jobs.models import Job, JobAssignment
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

MIGRATION = "apps.chat.migrations.0003_channel_application"
BEFORE_SPLIT = ("chat", "0002_message_application")


def _split() -> None:
    """Run the data step against the live models, as the jobs migration tests do."""
    import_module(MIGRATION).split_channels(django_apps, SimpleNamespace(connection=connection))


def _merge() -> None:
    import_module(MIGRATION).merge_channels(django_apps, SimpleNamespace(connection=connection))


def _submit(job: Job, recruiter: RecruiterProfile, name: str) -> Application:
    """An application with none of the channels or messages the chat signals add for it."""
    existing = set(Channel.objects.values_list("pk", flat=True))
    application = cast(
        Application,
        ApplicationFactory(
            job=job, recruiter=recruiter, candidate__owner=recruiter, candidate__full_name=name
        ),
    )
    Message.objects.filter(application=application).delete()
    Channel.objects.exclude(pk__in=existing).delete()
    # The signal's candidate thread is cached on `application.channel`; read it afresh so the
    # thread the split makes is the one found.
    return Application.objects.get(pk=application.pk)


def _message(
    channel: Channel,
    at: datetime,
    body: str,
    *,
    application: Application | None = None,
    author: User | None = None,
    kind: str = MessageKind.USER,
) -> Message:
    # `bulk_create` skips `Message.save`, so `last_message_at` is left for the migration to
    # set; `created_at` is `auto_now_add`, so the time is written with a follow-up update.
    [message] = Message.objects.bulk_create(
        [Message(channel=channel, application=application, author=author, kind=kind, body=body)]
    )
    Message.objects.filter(pk=message.pk).update(created_at=at)
    message.refresh_from_db()
    return message


def _before(job: Job, recruiter: RecruiterProfile, company_user: User) -> SimpleNamespace:
    """One channel for the pair, as every channel was before the split."""
    JobAssignment.objects.bulk_create([JobAssignment(job=job, recruiter=recruiter)])
    a = _submit(job, recruiter, "Phạm Minh Quân")
    b = _submit(job, recruiter, "Lê Thu Hà")
    general = Channel.objects.create(job=job, recruiter=recruiter)
    start = timezone.now() - timedelta(days=1)
    return SimpleNamespace(
        a=a,
        b=b,
        general=general,
        start=start,
        m5=_message(general, start, "NorthStar Talent joined this job", kind=MessageKind.SYSTEM),
        m1=_message(general, start + timedelta(hours=1), "Hi", author=company_user),
        m2=_message(
            general,
            start + timedelta(hours=2),
            "NorthStar Talent submitted Phạm Minh Quân",
            application=a,
            kind=MessageKind.SYSTEM,
        ),
        m3=_message(
            general,
            start + timedelta(hours=3),
            "Strong on payments",
            application=a,
            author=recruiter.user,
        ),
        m4=_message(
            general,
            start + timedelta(hours=4),
            "NorthStar Talent submitted Lê Thu Hà",
            application=b,
            kind=MessageKind.SYSTEM,
        ),
    )


def _messages(channel: Channel) -> set[Message]:
    return set(Message.objects.filter(channel=channel))


def _unread_total(job: Job, user: User) -> int:
    channels = with_unread_count(Channel.objects.filter(job=job), user)
    return sum(channel.unread_count for channel in channels)


def _snapshot() -> tuple[set[Any], set[Any], set[Any]]:
    return (
        set(
            Channel.objects.values_list("pk", "job", "recruiter", "application", "last_message_at")
        ),
        set(Message.objects.values_list("pk", "channel", "application", "created_at")),
        set(ChannelRead.objects.values_list("pk", "channel", "user", "last_read_at")),
    )


def test_split_keeps_every_message(
    job: Job, recruiter_profile: RecruiterProfile, company_ta: User
) -> None:
    made = _before(job, recruiter_profile, company_ta)
    count = Message.objects.count()

    _split()

    assert Message.objects.count() == count
    general = Channel.objects.get(job=job, recruiter=recruiter_profile, application__isnull=True)
    assert general == made.general
    assert _messages(general) == {made.m1, made.m5}
    assert _messages(made.a.channel) == {made.m2, made.m3}
    assert _messages(made.b.channel) == {made.m4}
    for application in (made.a, made.b):
        thread = application.channel
        assert (thread.job, thread.recruiter, thread.kind) == (job, recruiter_profile, "candidate")
    assert Channel.objects.filter(job=job).count() == 3


def test_split_copies_read_markers(
    job: Job, recruiter_profile: RecruiterProfile, company_ta: User, company_admin: User
) -> None:
    made = _before(job, recruiter_profile, company_ta)
    read_at = made.start + timedelta(hours=2, minutes=30)  # between m2 and m3
    ChannelRead.objects.create(channel=made.general, user=company_ta, last_read_at=read_at)
    readers = (company_ta, company_admin, recruiter_profile.user)
    unread = {user.pk: _unread_total(job, user) for user in readers}

    _split()

    markers = ChannelRead.objects.filter(user=company_ta)
    assert {(marker.channel, marker.last_read_at) for marker in markers} == {
        (made.general, read_at),
        (made.a.channel, read_at),
        (made.b.channel, read_at),
    }
    assert not ChannelRead.objects.filter(user__in=[company_admin, recruiter_profile.user]).exists()
    # No false unread count: the split moves messages between channels, never into "unread".
    assert {user.pk: _unread_total(job, user) for user in readers} == unread
    assert unread[company_ta.pk] == 2  # m3 and m4, after the marker


def test_split_sets_last_message_at(
    job: Job, recruiter_profile: RecruiterProfile, company_ta: User
) -> None:
    made = _before(job, recruiter_profile, company_ta)
    quiet = _submit(job, recruiter_profile, "Ngô Bảo Châu")
    stale = timezone.now()
    Channel.objects.filter(pk=made.general.pk).update(last_message_at=stale)
    empty = Channel.objects.create(
        job=job, recruiter=RecruiterProfileFactory(), last_message_at=stale
    )

    _split()

    def last(channel: Channel) -> datetime | None:
        channel.refresh_from_db()
        return channel.last_message_at

    assert last(made.general) == made.m1.created_at
    assert last(made.a.channel) == made.m3.created_at
    assert last(made.b.channel) == made.m4.created_at
    assert last(quiet.channel) is None
    assert last(empty) is None


def test_application_without_general_channel_gets_a_thread(
    job: Job, recruiter_profile: RecruiterProfile
) -> None:
    application = _submit(job, recruiter_profile, "Phạm Minh Quân")
    assert not Channel.objects.filter(job=job).exists()

    _split()

    thread = Channel.objects.get(job=job)
    assert (thread.application, thread.recruiter, thread.kind) == (
        application,
        recruiter_profile,
        "candidate",
    )
    assert thread.last_message_at is None
    assert not ChannelRead.objects.filter(channel=thread).exists()


def test_a_second_split_changes_nothing(
    job: Job, recruiter_profile: RecruiterProfile, company_ta: User
) -> None:
    made = _before(job, recruiter_profile, company_ta)
    ChannelRead.objects.create(
        channel=made.general, user=company_ta, last_read_at=made.start + timedelta(hours=2)
    )
    _split()
    after_first = _snapshot()

    _split()

    assert _snapshot() == after_first


def test_merge_puts_every_message_back(
    job: Job, recruiter_profile: RecruiterProfile, company_ta: User
) -> None:
    made = _before(job, recruiter_profile, company_ta)
    messages = set(Message.objects.all())
    _split()
    made.general.delete()  # The general channel is gone: merging must make it again.

    _merge()

    general = Channel.objects.get(job=job, recruiter=recruiter_profile)
    assert general.application is None
    assert _messages(general) == {made.m2, made.m3, made.m4}
    assert set(Message.objects.all()) == messages - {made.m1, made.m5}
    assert general.last_message_at == made.m4.created_at


def _leaves(executor: MigrationExecutor, **apps: str) -> list[tuple[str, str]]:
    """Every app at its latest migration, except the ones named, which stop where given."""
    return [(app, apps.get(app, name)) for app, name in executor.loader.graph.leaf_nodes()]


@pytest.mark.django_db(transaction=True)
def test_the_migration_runs_both_ways(
    job: Job, recruiter_profile: RecruiterProfile, company_ta: User
) -> None:
    """The real migration, on the historical models: back to one channel per pair, then split."""
    made = _before(job, recruiter_profile, company_ta)
    messages = set(Message.objects.values_list("pk", flat=True))
    executor = MigrationExecutor(connection)
    before = _leaves(executor, chat=BEFORE_SPLIT[1])
    try:
        _split()
        executor.migrate(before)
        executor.loader.build_graph()
        old = executor.loader.project_state(before).apps
        OldChannel = old.get_model("chat", "Channel")
        OldMessage = old.get_model("chat", "Message")
        assert list(OldChannel.objects.filter(job_id=job.pk).values_list("pk", flat=True)) == [
            made.general.pk
        ]
        assert set(OldMessage.objects.values_list("pk", flat=True)) == messages
        assert set(OldMessage.objects.values_list("channel_id", flat=True)) == {made.general.pk}
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    assert set(Message.objects.values_list("pk", flat=True)) == messages
    assert _messages(made.general) == {made.m1, made.m5}
    assert _messages(Application.objects.get(pk=made.a.pk).channel) == {made.m2, made.m3}
    assert _messages(Application.objects.get(pk=made.b.pk).channel) == {made.m4}
