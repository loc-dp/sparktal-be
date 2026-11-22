import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from django.db import connection, transaction

from apps.candidates.models import Application
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.chat.models import Channel, MessageKind
from apps.chat.signals import ensure_candidate_channel, ensure_channel
from apps.companies.models import Company
from apps.hiring import discussion
from apps.jobs.models import Job
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db


def _general(job: Job, recruiter: RecruiterProfile) -> Channel:
    return Channel.objects.get(job=job, recruiter=recruiter, application=None)


def test_submitting_a_cv_opens_the_candidate_thread(
    company: Company, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company)
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    candidate = CandidateFactory(owner=recruiter_profile, full_name="Phạm Minh Quân")
    application = ApplicationFactory(job=job, candidate=candidate, recruiter=recruiter_profile)

    thread = Channel.objects.get(application=application)
    assert (thread.job, thread.recruiter, thread.kind) == (job, recruiter_profile, "candidate")
    message = thread.messages.get()
    assert message.kind == MessageKind.SYSTEM
    assert message.body == f"{recruiter_profile.nickname} submitted Phạm Minh Quân"
    assert message.application == application
    general = _general(job, recruiter_profile)
    assert [m.body for m in general.messages.all()] == [
        f"{recruiter_profile.nickname} joined this job"
    ]


def test_assignment_opens_the_general_thread(
    company: Company, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company)
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    channel = Channel.objects.get(job=job, recruiter=recruiter_profile)
    assert channel.kind == "general"
    message = channel.messages.get()
    assert message.kind == MessageKind.SYSTEM
    assert message.application is None
    assert message.body == f"{recruiter_profile.nickname} joined this job"


def test_each_application_gets_its_own_thread(
    company: Company, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company)
    first = ApplicationFactory(job=job, recruiter=recruiter_profile)
    second = ApplicationFactory(job=job, recruiter=recruiter_profile)
    threads = Channel.objects.filter(job=job, recruiter=recruiter_profile)
    assert {channel.application_id for channel in threads} == {first.pk, second.pk}
    for application in (first, second):
        assert [m.application_id for m in application.channel.messages.all()] == [application.pk]


def test_updating_an_application_posts_nothing(
    company: Company, recruiter_profile: RecruiterProfile
) -> None:
    application = ApplicationFactory(job=JobFactory(company=company), recruiter=recruiter_profile)
    application.stage = "screen"
    application.save(update_fields=["stage"])
    assert application.channel.messages.count() == 1


def test_two_recruiters_on_one_job_get_two_channels(company: Company) -> None:
    job = JobFactory(company=company)
    ApplicationFactory(job=job)
    ApplicationFactory(job=job)
    assert Channel.objects.filter(job=job).count() == 2


def test_ensure_channel_is_the_general_thread_beside_candidate_threads(
    company: Company, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company)
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    ApplicationFactory(job=job, recruiter=recruiter_profile)
    ApplicationFactory(job=job, recruiter=recruiter_profile)
    channel, created = ensure_channel(job, recruiter_profile)
    assert not created
    assert channel == _general(job, recruiter_profile)


def test_ensure_channel_opens_a_general_thread_when_only_candidate_threads_exist(
    company: Company, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company)
    application = ApplicationFactory(job=job, recruiter=recruiter_profile)
    channel, created = ensure_channel(job, recruiter_profile)
    assert created
    assert channel.kind == "general"
    assert channel != application.channel


def test_ensure_candidate_channel_reuses_the_thread(
    company: Company, recruiter_profile: RecruiterProfile
) -> None:
    application = ApplicationFactory(job=JobFactory(company=company), recruiter=recruiter_profile)
    channel, created = ensure_candidate_channel(application)
    assert not created
    assert channel == application.channel


def test_stage_change_message_lands_in_the_candidate_thread(
    company: Company, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company)
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    application = ApplicationFactory(job=job, recruiter=recruiter_profile)
    message = discussion.post_system_message(application, "Moved to Screen")
    assert message.channel == application.channel
    assert message.application == application
    assert message.kind == MessageKind.SYSTEM
    assert not _general(job, recruiter_profile).messages.filter(body="Moved to Screen").exists()


# --- Two callers make the same thread at once (plan Review Focus 2).


def _race(first: Callable[[], Any], second: Callable[[], Any]) -> None:
    """Run `first` in a transaction that stays open until `second`, in its own transaction, is
    blocked on the row `first` inserted; then let `first` commit. Neither may raise."""
    holding, release = threading.Event(), threading.Event()
    errors: list[Exception] = []

    def holder() -> None:
        try:
            with transaction.atomic():
                first()
                holding.set()
                release.wait(15)
        except Exception as error:  # reported by the assertion below
            errors.append(error)
        finally:
            holding.set()
            connection.close()

    def latecomer() -> None:
        try:
            with transaction.atomic():
                second()
                # The caller's transaction is still usable after the lost insert.
                Channel.objects.count()
        except Exception as error:
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=holder), threading.Thread(target=latecomer)]
    try:
        threads[0].start()
        assert holding.wait(15)
        threads[1].start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
                if cursor.fetchone()[0]:
                    break
            time.sleep(0.05)
        else:
            pytest.fail("the second caller never waited for the first one's row")
    finally:
        release.set()
        for thread in threads:
            thread.join(15)
    assert errors == []


@pytest.mark.django_db(transaction=True)
def test_ensure_candidate_channel_is_race_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stage change posts a system message while a user sends the first message."""
    monkeypatch.setattr(discussion, "broadcast_message", lambda message: None)
    application: Application = ApplicationFactory()
    Channel.objects.filter(application=application).delete()  # as if no thread existed yet
    author = RecruiterProfileFactory().user

    _race(
        lambda: discussion.post_system_message(application, "Moved to Screen"),
        lambda: discussion.post_message(application, author=author, body="Hi"),
    )

    thread = Channel.objects.get(application=application)
    assert sorted(m.body for m in thread.messages.all()) == ["Hi", "Moved to Screen"]
    assert ensure_candidate_channel(application) == (thread, False)


@pytest.mark.django_db(transaction=True)
def test_ensure_channel_is_race_safe() -> None:
    job, recruiter = JobFactory(), RecruiterProfileFactory()
    ApplicationFactory(job=job, recruiter=recruiter)  # a candidate thread must not get in the way
    seen: list[tuple[Channel, bool]] = []

    _race(
        lambda: seen.append(ensure_channel(job, recruiter)),
        lambda: seen.append(ensure_channel(job, recruiter)),
    )

    general = Channel.objects.get(job=job, recruiter=recruiter, application=None)
    assert sorted(seen, key=lambda pair: pair[1]) == [(general, False), (general, True)]
