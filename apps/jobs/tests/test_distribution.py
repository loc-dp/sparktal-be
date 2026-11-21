import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any, cast

import pytest
from django.db import connection
from django.http import Http404
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.chat.models import Channel
from apps.jobs.access import can_submit_to_job, jobs_open_to
from apps.jobs.distribution import (
    DistributionError,
    assign_recruiters,
    distribute_job,
    join_job,
    set_bounty,
    set_marketplace,
    unassign_recruiter,
)
from apps.jobs.models import BountyType, Job, JobAssignment, JobStatus
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory
from apps.recruiters.models import RecruiterProfile, RecruiterStatus
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

Commit = Callable[..., Any]

NEEDS_BOUNTY = "Set the bounty before distributing."
NEEDS_RECRUITER = "Pick at least one recruiter."
LAST_RECRUITER = "A live job needs at least one recruiter. Assign someone else first."
NOT_WAITING = "This job is no longer waiting for distribution."
NOT_POSTED = "This job hasn't been posted by the company yet."
NOT_DISTRIBUTED = "This job hasn't been distributed yet."
NOT_LIVE = "Only a live job can take recruiters."


def _queued(**fields: Any) -> Job:
    """A job the company posted: waiting for Sparktal, with no bounty yet."""
    values: dict[str, Any] = {
        "status": JobStatus.PENDING,
        "published_at": None,
        "on_marketplace": False,
        "bounty_type": None,
        "bounty_pct": None,
        "posted_at": timezone.now(),
    }
    return cast(Job, JobFactory(**{**values, **fields}))


def _recruiter(email: str, **fields: Any) -> RecruiterProfile:
    return cast(RecruiterProfile, RecruiterProfileFactory(user__email=email, **fields))


def _assign(job: Job, *recruiters: RecruiterProfile) -> list[JobAssignment]:
    return [
        cast(JobAssignment, JobAssignmentFactory(job=job, recruiter=recruiter))
        for recruiter in recruiters
    ]


@pytest.fixture
def staff(db: None) -> User:
    return cast(User, UserFactory(email="long.mai@sparktal.com", is_staff=True))


@pytest.fixture
def pending_job(db: None) -> Job:
    """Waiting in the Sparktal queue, the bounty already set."""
    return _queued(bounty_type=BountyType.PCT, bounty_pct=18)


@pytest.fixture
def live_job(db: None) -> Job:
    return cast(
        Job, JobFactory(title="Payments Lead", company__name="Orbit Pay", on_marketplace=False)
    )


@pytest.fixture
def closed_job(db: None) -> Job:
    return cast(Job, JobFactory(status=JobStatus.CLOSED, on_marketplace=False))


@pytest.fixture
def approved(db: None) -> RecruiterProfile:
    return _recruiter("bluefin@example.com", nickname="Bluefin")


@pytest.fixture
def two_approved(db: None) -> list[RecruiterProfile]:
    return [
        _recruiter("bluefin@example.com", nickname="Bluefin"),
        _recruiter("northstar@example.com", nickname="NorthStar"),
    ]


@pytest.fixture
def pending_recruiter(db: None) -> RecruiterProfile:
    return _recruiter("atlas@example.com", status=RecruiterStatus.PENDING)


@pytest.fixture
def two_assigned(live_job: Job, two_approved: list[RecruiterProfile]) -> list[JobAssignment]:
    return _assign(live_job, *two_approved)


# --- Distribute --------------------------------------------------------------------------------


def test_distribute_needs_bounty(staff: User, approved: RecruiterProfile) -> None:
    job = _queued()

    with pytest.raises(DistributionError) as error:
        distribute_job(job, actor=staff, recruiters=[approved])

    assert str(error.value.detail) == NEEDS_BOUNTY
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING


def test_distribute_needs_a_recruiter(pending_job: Job, staff: User) -> None:
    with pytest.raises(DistributionError) as error:
        distribute_job(pending_job, actor=staff, recruiters=[], on_marketplace=True)

    assert str(error.value.detail) == NEEDS_RECRUITER
    pending_job.refresh_from_db()
    assert pending_job.status == JobStatus.PENDING
    assert pending_job.published_at is None


def test_distribute_assigns_and_goes_live(
    pending_job: Job, staff: User, approved: RecruiterProfile
) -> None:
    distributed = distribute_job(pending_job, actor=staff, recruiters=[approved])

    pending_job.refresh_from_db()
    assert distributed.pk == pending_job.pk
    assert pending_job.status == JobStatus.ACTIVE
    assert pending_job.published_at is not None
    held = JobAssignment.objects.filter(job=pending_job, left_at__isnull=True)
    assert [assignment.recruiter_id for assignment in held] == [approved.pk]
    assert Channel.objects.filter(job=pending_job, recruiter=approved).exists()


def test_distribute_ignores_marketplace_when_off(
    pending_job: Job, staff: User, approved: RecruiterProfile, settings: Any
) -> None:
    settings.MARKETPLACE_ENABLED = False

    distribute_job(pending_job, actor=staff, recruiters=[approved], on_marketplace=True)

    pending_job.refresh_from_db()
    assert pending_job.status == JobStatus.ACTIVE
    assert pending_job.on_marketplace is False


def test_distribute_keeps_marketplace_when_on(
    pending_job: Job, staff: User, approved: RecruiterProfile, settings: Any
) -> None:
    settings.MARKETPLACE_ENABLED = True

    distribute_job(pending_job, actor=staff, recruiters=[approved], on_marketplace=True)

    pending_job.refresh_from_db()
    assert pending_job.on_marketplace is True


def test_distribute_twice_is_rejected(
    pending_job: Job, staff: User, approved: RecruiterProfile
) -> None:
    distribute_job(pending_job, actor=staff, recruiters=[approved])

    with pytest.raises(DistributionError) as error:
        distribute_job(pending_job, actor=staff, recruiters=[approved])

    assert str(error.value.detail) == NOT_WAITING


def test_distribute_with_an_unapproved_recruiter_changes_nothing(
    pending_job: Job, staff: User, pending_recruiter: RecruiterProfile
) -> None:
    with pytest.raises(ValidationError):
        distribute_job(pending_job, actor=staff, recruiters=[pending_recruiter])

    pending_job.refresh_from_db()
    assert pending_job.status == JobStatus.PENDING
    assert not JobAssignment.objects.filter(job=pending_job).exists()


# --- Assign ------------------------------------------------------------------------------------


def test_assign_is_immediate(live_job: Job, staff: User, approved: RecruiterProfile) -> None:
    assert not can_submit_to_job(approved, live_job)

    assigned = assign_recruiters(live_job, actor=staff, recruiters=[approved])

    assert [assignment.recruiter_id for assignment in assigned] == [approved.pk]
    assert assigned[0].left_at is None
    # No accept step: the job is theirs at once.
    assert jobs_open_to(approved).filter(pk=live_job.pk).exists()
    assert can_submit_to_job(approved, live_job)


def test_assign_skips_recruiter_already_on(
    live_job: Job,
    staff: User,
    approved: RecruiterProfile,
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Commit,
) -> None:
    (assigned,) = _assign(live_job, approved)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        newly = assign_recruiters(live_job, actor=staff, recruiters=[approved])

    assert newly == []
    assert list(JobAssignment.objects.filter(job=live_job)) == [assigned]
    assert callbacks == []
    assert mailoutbox == []


def test_assign_reopens_a_removed_recruiter(
    live_job: Job, staff: User, approved: RecruiterProfile
) -> None:
    removed = cast(
        JobAssignment,
        JobAssignmentFactory(job=live_job, recruiter=approved, left_at=timezone.now()),
    )

    (reopened,) = assign_recruiters(live_job, actor=staff, recruiters=[approved])

    assert reopened.pk == removed.pk
    removed.refresh_from_db()
    assert removed.left_at is None
    assert JobAssignment.objects.filter(job=live_job).count() == 1


def test_assign_putting_a_removed_recruiter_back_restarts_joined_at(
    live_job: Job, staff: User, approved: RecruiterProfile
) -> None:
    removed = cast(
        JobAssignment,
        JobAssignmentFactory(job=live_job, recruiter=approved, left_at=timezone.now()),
    )
    first_joined = removed.joined_at - timedelta(days=30)
    JobAssignment.objects.filter(pk=removed.pk).update(joined_at=first_joined)
    before = timezone.now()

    assign_recruiters(live_job, actor=staff, recruiters=[approved])

    removed.refresh_from_db()
    assert removed.left_at is None
    assert removed.joined_at >= before


def test_assign_refuses_unapproved(
    live_job: Job,
    staff: User,
    approved: RecruiterProfile,
    pending_recruiter: RecruiterProfile,
) -> None:
    with pytest.raises(ValidationError) as error:
        assign_recruiters(live_job, actor=staff, recruiters=[approved, pending_recruiter])

    assert error.value.detail == {"recruiters": ["Only approved recruiters can be assigned."]}
    assert not JobAssignment.objects.filter(job=live_job).exists()


@pytest.mark.parametrize("state", ["closed", "deleted", "pending"])
def test_assign_needs_a_live_job(state: str, staff: User, approved: RecruiterProfile) -> None:
    if state == "deleted":
        job = cast(Job, JobFactory(deleted_at=timezone.now()))
    elif state == "pending":
        job = _queued(bounty_type=BountyType.PCT, bounty_pct=18)
    else:
        job = cast(Job, JobFactory(status=JobStatus.CLOSED))

    with pytest.raises(DistributionError) as error:
        assign_recruiters(job, actor=staff, recruiters=[approved])

    assert str(error.value.detail) == NOT_LIVE
    assert not JobAssignment.objects.filter(job=job).exists()


def test_assign_needs_a_bounty(
    staff: User, approved: RecruiterProfile, mailoutbox: list[Any]
) -> None:
    job = cast(Job, JobFactory(bounty_type=None, bounty_pct=None))

    with pytest.raises(DistributionError) as error:
        assign_recruiters(job, actor=staff, recruiters=[approved])

    assert str(error.value.detail) == NEEDS_BOUNTY
    assert not JobAssignment.objects.filter(job=job).exists()
    assert mailoutbox == []


def test_assign_first_recruiter_to_unstaffed_live_job(
    live_job: Job, staff: User, approved: RecruiterProfile
) -> None:
    assert not JobAssignment.objects.filter(job=live_job).exists()

    assign_recruiters(live_job, actor=staff, recruiters=[approved])

    assert JobAssignment.objects.filter(job=live_job, left_at__isnull=True).count() == 1


def test_assign_emails_each_new_recruiter(
    live_job: Job,
    staff: User,
    two_approved: list[RecruiterProfile],
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Commit,
    settings: Any,
) -> None:
    settings.FRONTEND_URL = "https://app.sparktal.test"

    with django_capture_on_commit_callbacks(execute=True):
        assign_recruiters(live_job, actor=staff, recruiters=two_approved)

    assert sorted(message.to[0] for message in mailoutbox) == [
        "bluefin@example.com",
        "northstar@example.com",
    ]
    url = f"https://app.sparktal.test/r/jobs/{live_job.pk}"
    for message in mailoutbox:
        assert message.subject == "You have been assigned a job: Payments Lead"
        assert url in message.body
        assert url in message.alternatives[0][0]
        assert "18% of annual salary" in message.body
        assert "Open the job" in message.alternatives[0][0]
        assert "/r/marketplace/" not in message.body


# --- Remove ------------------------------------------------------------------------------------


def test_unassign_sets_left_at(live_job: Job, two_assigned: list[JobAssignment]) -> None:
    first, second = two_assigned

    removed = unassign_recruiter(live_job, recruiter_id=first.recruiter_id)

    assert removed.pk == first.pk
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.left_at is not None
    assert second.left_at is None


def test_last_recruiter_cannot_be_removed(live_job: Job, approved: RecruiterProfile) -> None:
    (only,) = _assign(live_job, approved)
    # Someone removed earlier does not count as still on the job.
    JobAssignmentFactory(job=live_job, left_at=timezone.now())

    with pytest.raises(DistributionError) as error:
        unassign_recruiter(live_job, recruiter_id=approved.pk)

    assert str(error.value.detail) == LAST_RECRUITER
    only.refresh_from_db()
    assert only.left_at is None


def test_last_recruiter_of_closed_job_can_be_removed(
    closed_job: Job, approved: RecruiterProfile
) -> None:
    (only,) = _assign(closed_job, approved)

    unassign_recruiter(closed_job, recruiter_id=approved.pk)

    only.refresh_from_db()
    assert only.left_at is not None


def test_unassign_unknown_recruiter_is_404(live_job: Job, approved: RecruiterProfile) -> None:
    _assign(live_job, _recruiter("northstar@example.com"))
    with pytest.raises(Http404):
        unassign_recruiter(live_job, recruiter_id=approved.pk)

    # One already removed holds nothing either.
    JobAssignmentFactory(job=live_job, recruiter=approved, left_at=timezone.now())
    with pytest.raises(Http404):
        unassign_recruiter(live_job, recruiter_id=approved.pk)


def test_unassign_sends_no_email(
    live_job: Job,
    two_assigned: list[JobAssignment],
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Commit,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        unassign_recruiter(live_job, recruiter_id=two_assigned[0].recruiter_id)

    assert mailoutbox == []


@pytest.mark.django_db(transaction=True)
def test_concurrent_removals_keep_one(live_job: Job, two_assigned: list[JobAssignment]) -> None:
    """Two removals at once, one recruiter each: the job lock lets only one of them through."""
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def remove(recruiter_id: Any) -> None:
        try:
            barrier.wait()
            unassign_recruiter(live_job, recruiter_id=recruiter_id)
            outcomes.append("removed")
        except DistributionError as error:
            outcomes.append(str(error.detail))
        finally:
            connection.close()

    threads = [
        threading.Thread(target=remove, args=(assignment.recruiter_id,))
        for assignment in two_assigned
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(outcomes) == sorted(["removed", LAST_RECRUITER])
    assert JobAssignment.objects.filter(job=live_job, left_at__isnull=True).count() == 1


# --- Bounty and marketplace ----------------------------------------------------------------------


@pytest.mark.parametrize("state", ["draft", "submitted", "deleted"])
def test_set_bounty_on_draft_is_rejected(state: str) -> None:
    if state == "deleted":
        job = cast(Job, JobFactory(status=JobStatus.CLOSED, deleted_at=timezone.now()))
    else:
        job = _queued(status=state, posted_at=None)

    with pytest.raises(DistributionError) as error:
        set_bounty(job, bounty_type=BountyType.PCT, bounty_pct=20)

    assert str(error.value.detail) == NOT_POSTED
    job.refresh_from_db()
    assert job.bounty_pct != 20


def test_set_bounty_checks_the_rules() -> None:
    job = _queued()

    with pytest.raises(ValidationError) as error:
        set_bounty(job, bounty_type=BountyType.PCT, bounty_pct=40)

    assert "bounty_pct" in error.value.detail
    job.refresh_from_db()
    assert job.bounty_type is None


def test_set_bounty_applies_to_existing_assignments(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
    settings: Any,
) -> None:
    settings.MARKETPLACE_ENABLED = True  # the detail read here is a marketplace route
    job = cast(Job, JobFactory(bounty_type=BountyType.PCT, bounty_pct=18))
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)

    set_bounty(job, bounty_type=BountyType.PCT, bounty_pct=25)

    detail = client_for(recruiter_user).get(f"/api/v1/marketplace/jobs/{job.pk}").json()
    assert detail["joined"] is True
    assert detail["bounty_type"] == "pct"
    assert detail["bounty_pct"] == 25

    set_bounty(job, bounty_type=BountyType.FIXED, bounty_amount=90_000_000)

    detail = client_for(recruiter_user).get(f"/api/v1/marketplace/jobs/{job.pk}").json()
    assert detail["bounty_type"] == "fixed"
    assert detail["bounty_pct"] is None
    assert detail["bounty_amount"] == "90000000"


def test_set_marketplace_only_on_a_distributed_job() -> None:
    live = cast(Job, JobFactory(on_marketplace=True))

    set_marketplace(live, on=False)

    live.refresh_from_db()
    assert live.on_marketplace is False
    deleted = cast(
        Job,
        JobFactory(status=JobStatus.CLOSED, deleted_at=timezone.now(), on_marketplace=False),
    )
    for job in (_queued(), deleted):
        with pytest.raises(DistributionError) as error:
            set_marketplace(job, on=True)
        assert str(error.value.detail) == NOT_DISTRIBUTED
        job.refresh_from_db()
        assert job.on_marketplace is False


def test_join_job_puts_the_recruiter_on_with_a_channel(
    recruiter_profile: RecruiterProfile,
) -> None:
    job = cast(Job, JobFactory())

    assignment, created = join_job(job, recruiter_profile)

    assert created is True
    assert assignment.is_active
    assert Channel.objects.filter(job=job, recruiter=recruiter_profile).exists()


def _first_statement(action: Callable[[], Any]) -> str:
    """The first SQL `action` runs, past the savepoint `transaction.atomic` opens."""
    with CaptureQueriesContext(connection) as queries:
        action()
    statements = [query["sql"] for query in queries if "SAVEPOINT" not in query["sql"]]
    return statements[0]


@pytest.mark.parametrize("action", ["join", "assign", "unassign", "set_bounty", "set_marketplace"])
def test_every_change_locks_the_job_first(
    action: str, staff: User, recruiter_profile: RecruiterProfile
) -> None:
    """One lock order for all of them (job, then assignment), as `close_job` uses.

    Without it a marketplace join and a removal on one job could deadlock, or the last
    recruiter could be removed by two removals that each saw the other still on the job.
    """
    job = cast(Job, JobFactory())
    _assign(job, recruiter_profile, _recruiter("atlas@example.com"))
    other = _recruiter("bluefin@example.com")
    run: dict[str, Callable[[], Any]] = {
        "join": lambda: join_job(job, other),
        "assign": lambda: assign_recruiters(job, actor=staff, recruiters=[other]),
        "unassign": lambda: unassign_recruiter(job, recruiter_id=recruiter_profile.pk),
        "set_bounty": lambda: set_bounty(job, bounty_type=BountyType.PCT, bounty_pct=20),
        "set_marketplace": lambda: set_marketplace(job, on=False),
    }

    first = _first_statement(run[action])

    assert first.startswith('SELECT "jobs_job"')
    assert first.endswith("FOR UPDATE")
