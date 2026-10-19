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
from apps.jobs.distribution import (
    DistributionError,
    accept_invite,
    cancel_invites,
    decline_invite,
    distribute_job,
    invite_recruiters,
    join_job,
    set_bounty,
    set_marketplace,
)
from apps.jobs.models import BountyType, InviteStatus, Job, JobAssignment, JobInvite, JobStatus
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory, JobInviteFactory
from apps.recruiters.models import RecruiterProfile, RecruiterStatus
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

Commit = Callable[..., Any]

NEEDS_BOUNTY = "Set the bounty before distributing."
NEEDS_CHANNEL = "Pick the marketplace, at least one recruiter, or both."
NOT_WAITING = "This job is no longer waiting for distribution."
NOT_POSTED = "This job hasn't been posted by the company yet."
NOT_DISTRIBUTED = "This job hasn't been distributed yet."
NOT_LIVE = "Only a live job can take invites."
INVITE_CLOSED = "This invite is no longer open."


@pytest.fixture
def ops(db: None) -> User:
    return cast(User, UserFactory(email="long.mai@sparktal.com", is_staff=True))


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


def test_distribute_needs_bounty(ops: User) -> None:
    job = _queued()

    with pytest.raises(DistributionError) as error:
        distribute_job(job, actor=ops, on_marketplace=True, recruiters=[])

    assert str(error.value.detail) == NEEDS_BOUNTY
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING


def test_distribute_needs_a_channel(ops: User) -> None:
    job = _queued(bounty_type=BountyType.PCT, bounty_pct=15)

    with pytest.raises(DistributionError) as error:
        distribute_job(job, actor=ops, on_marketplace=False, recruiters=[])

    assert str(error.value.detail) == NEEDS_CHANNEL
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING


def test_distribute_marketplace_and_invites(
    ops: User, mailoutbox: list[Any], django_capture_on_commit_callbacks: Commit
) -> None:
    job = _queued(title="Payments Lead", company__name="Orbit Pay")
    first = _recruiter("bluefin@example.com", nickname="Bluefin")
    second = _recruiter("northstar@example.com", nickname="NorthStar")
    set_bounty(job, bounty_type=BountyType.PCT, bounty_pct=18)

    with django_capture_on_commit_callbacks(execute=True):
        distributed = distribute_job(
            job, actor=ops, on_marketplace=True, recruiters=[first, second]
        )

    job.refresh_from_db()
    assert distributed.pk == job.pk
    assert job.status == JobStatus.ACTIVE
    assert job.published_at is not None
    assert job.on_marketplace is True
    invites = JobInvite.objects.filter(job=job)
    assert sorted(invite.recruiter.nickname for invite in invites) == ["Bluefin", "NorthStar"]
    assert {invite.status for invite in invites} == {InviteStatus.PENDING}
    assert {invite.invited_by_id for invite in invites} == {ops.pk}
    assert sorted(message.to[0] for message in mailoutbox) == [
        "bluefin@example.com",
        "northstar@example.com",
    ]
    message = mailoutbox[0]
    assert message.subject == "Orbit Pay job for you: Payments Lead"
    assert f"/r/marketplace/{job.pk}" in message.body
    assert "18% of annual salary" in message.body
    assert "18% of annual salary" in message.alternatives[0][0]


def test_distribute_twice_is_rejected(ops: User) -> None:
    job = _queued(bounty_type=BountyType.PCT, bounty_pct=15)
    distribute_job(job, actor=ops, on_marketplace=True, recruiters=[])

    with pytest.raises(DistributionError) as error:
        distribute_job(job, actor=ops, on_marketplace=True, recruiters=[])

    assert str(error.value.detail) == NOT_WAITING


def test_distribute_with_an_unapproved_recruiter_changes_nothing(ops: User) -> None:
    job = _queued(bounty_type=BountyType.PCT, bounty_pct=15)
    waiting = _recruiter("atlas@example.com", status=RecruiterStatus.PENDING)

    with pytest.raises(ValidationError):
        distribute_job(job, actor=ops, on_marketplace=True, recruiters=[waiting])

    job.refresh_from_db()
    assert job.status == JobStatus.PENDING
    assert not JobInvite.objects.filter(job=job).exists()


def test_invite_rejects_unapproved_recruiter(ops: User) -> None:
    job = cast(Job, JobFactory())
    approved = _recruiter("bluefin@example.com")
    waiting = _recruiter("atlas@example.com", status=RecruiterStatus.PENDING)

    with pytest.raises(ValidationError) as error:
        invite_recruiters(job, actor=ops, recruiters=[approved, waiting])

    assert error.value.detail == {"recruiters": ["Only approved recruiters can be invited."]}
    assert not JobInvite.objects.filter(job=job).exists()


@pytest.mark.parametrize("state", ["closed", "deleted"])
def test_invite_needs_an_active_job(state: str, ops: User) -> None:
    if state == "deleted":
        job = cast(Job, JobFactory(deleted_at=timezone.now()))
    else:
        job = cast(Job, JobFactory(status=JobStatus.CLOSED))

    with pytest.raises(DistributionError) as error:
        invite_recruiters(job, actor=ops, recruiters=[_recruiter("bluefin@example.com")])

    assert str(error.value.detail) == NOT_LIVE
    assert not JobInvite.objects.filter(job=job).exists()


def test_invite_needs_a_bounty(ops: User, mailoutbox: list[Any]) -> None:
    job = cast(Job, JobFactory(bounty_type=None, bounty_pct=None))

    with pytest.raises(DistributionError) as error:
        invite_recruiters(job, actor=ops, recruiters=[_recruiter("bluefin@example.com")])

    assert str(error.value.detail) == NEEDS_BOUNTY
    assert not JobInvite.objects.filter(job=job).exists()
    assert mailoutbox == []


@pytest.mark.parametrize("previous", ["declined", "cancelled", "accepted_then_left"])
def test_reinvite_after_decline_resets_to_pending(
    previous: str, ops: User, mailoutbox: list[Any], django_capture_on_commit_callbacks: Commit
) -> None:
    job = cast(Job, JobFactory())
    recruiter = _recruiter("bluefin@example.com")
    status = InviteStatus.ACCEPTED if previous == "accepted_then_left" else previous
    old = JobInviteFactory(
        job=job,
        recruiter=recruiter,
        status=status,
        decline_reason="Too busy this month" if previous == "declined" else "",
        responded_at=timezone.now() - timedelta(days=3),
    )
    if previous == "accepted_then_left":
        JobAssignmentFactory(job=job, recruiter=recruiter, left_at=timezone.now())

    with django_capture_on_commit_callbacks(execute=True):
        invited = invite_recruiters(job, actor=ops, recruiters=[recruiter])

    assert [invite.pk for invite in invited] == [old.pk]
    old.refresh_from_db()
    assert old.status == InviteStatus.PENDING
    assert old.responded_at is None
    assert old.decline_reason == ""
    assert old.invited_by_id == ops.pk
    assert JobInvite.objects.filter(job=job).count() == 1
    assert [message.to for message in mailoutbox] == [["bluefin@example.com"]]
    # Still pending: asking again sends nothing new.
    assert invite_recruiters(job, actor=ops, recruiters=[recruiter]) == []


def test_invite_skips_recruiter_already_working(ops: User) -> None:
    job = cast(Job, JobFactory())
    working = _recruiter("northstar@example.com")
    JobAssignmentFactory(job=job, recruiter=working)

    assert invite_recruiters(job, actor=ops, recruiters=[working]) == []
    assert not JobInvite.objects.filter(job=job).exists()


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
) -> None:
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


def test_cancel_invites_moves_only_pending_ones() -> None:
    job = cast(Job, JobFactory())
    pending = JobInviteFactory(job=job)
    declined = JobInviteFactory(job=job, status=InviteStatus.DECLINED)
    elsewhere = JobInviteFactory()

    cancelled = cancel_invites(job, invite_ids=[pending.pk, declined.pk, elsewhere.pk])

    assert cancelled == 1
    statuses = dict(JobInvite.objects.values_list("pk", "status"))
    assert statuses == {
        pending.pk: InviteStatus.CANCELLED,
        declined.pk: InviteStatus.DECLINED,
        elsewhere.pk: InviteStatus.PENDING,
    }


def test_join_job_settles_a_pending_invite(recruiter_profile: RecruiterProfile) -> None:
    job = cast(Job, JobFactory())
    invite = JobInviteFactory(job=job, recruiter=recruiter_profile)

    assignment, created = join_job(job, recruiter_profile)

    assert created is True
    assert assignment.is_active
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED
    assert invite.responded_at is not None
    assert Channel.objects.filter(job=job, recruiter=recruiter_profile).exists()


def test_accept_invite_joins_the_job(recruiter_profile: RecruiterProfile) -> None:
    invite = JobInviteFactory(recruiter=recruiter_profile)

    assignment = accept_invite(invite, recruiter=recruiter_profile)

    assert assignment.job_id == invite.job_id
    assert assignment.is_active
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED
    assert invite.responded_at is not None


def test_accept_another_recruiters_invite_is_404(recruiter_profile: RecruiterProfile) -> None:
    invite = JobInviteFactory()

    with pytest.raises(Http404):
        accept_invite(invite, recruiter=recruiter_profile)
    with pytest.raises(Http404):
        decline_invite(invite, recruiter=recruiter_profile)


@pytest.mark.parametrize("stale", ["cancelled", "job_closed", "job_deleted", "no_bounty"])
def test_accept_invite_no_longer_open(stale: str, recruiter_profile: RecruiterProfile) -> None:
    invite = JobInviteFactory(recruiter=recruiter_profile)
    if stale == "cancelled":
        cancel_invites(invite.job, invite_ids=[invite.pk])
    elif stale == "no_bounty":
        Job.objects.filter(pk=invite.job_id).update(bounty_type=None, bounty_pct=None)
    else:
        job = invite.job
        job.status = JobStatus.CLOSED
        job.deleted_at = timezone.now() if stale == "job_deleted" else None
        job.save()

    with pytest.raises(DistributionError) as error:
        accept_invite(invite, recruiter=recruiter_profile)

    assert str(error.value.detail) == INVITE_CLOSED
    assert not JobAssignment.objects.filter(recruiter=recruiter_profile).exists()


def test_decline_invite_keeps_a_trimmed_reason(recruiter_profile: RecruiterProfile) -> None:
    invite = JobInviteFactory(recruiter=recruiter_profile)

    declined = decline_invite(invite, recruiter=recruiter_profile, reason="  " + "x" * 600 + " ")

    assert declined.status == InviteStatus.DECLINED
    assert declined.decline_reason == "x" * 500
    assert declined.responded_at is not None
    with pytest.raises(DistributionError) as error:
        accept_invite(invite, recruiter=recruiter_profile)
    assert str(error.value.detail) == INVITE_CLOSED


def _first_statement(action: Callable[[], Any]) -> str:
    """The first SQL `action` runs, past the savepoint `transaction.atomic` opens."""
    with CaptureQueriesContext(connection) as queries:
        action()
    statements = [query["sql"] for query in queries if "SAVEPOINT" not in query["sql"]]
    return statements[0]


@pytest.mark.parametrize(
    "action", ["join", "accept", "decline", "invite", "cancel", "set_bounty", "set_marketplace"]
)
def test_every_change_locks_the_job_first(
    action: str, ops: User, recruiter_profile: RecruiterProfile
) -> None:
    """One lock order for all of them (job, then invite or assignment), as `close_job` uses.

    Without it a marketplace join and an invite accept on one job deadlock, and an invite can
    go out to a recruiter who joined a moment before.
    """
    job = cast(Job, JobFactory())
    invite = JobInviteFactory(job=job, recruiter=recruiter_profile)
    other = _recruiter("bluefin@example.com")
    run: dict[str, Callable[[], Any]] = {
        "join": lambda: join_job(job, recruiter_profile),
        "accept": lambda: accept_invite(invite, recruiter=recruiter_profile),
        "decline": lambda: decline_invite(invite, recruiter=recruiter_profile),
        "invite": lambda: invite_recruiters(job, actor=ops, recruiters=[other]),
        "cancel": lambda: cancel_invites(job, invite_ids=[invite.pk]),
        "set_bounty": lambda: set_bounty(job, bounty_type=BountyType.PCT, bounty_pct=20),
        "set_marketplace": lambda: set_marketplace(job, on=False),
    }

    first = _first_statement(run[action])

    assert first.startswith('SELECT "jobs_job"')
    assert first.endswith("FOR UPDATE")


def test_invite_after_a_marketplace_join_skips_the_recruiter(
    ops: User, recruiter_profile: RecruiterProfile, mailoutbox: list[Any]
) -> None:
    job = cast(Job, JobFactory())
    join_job(job, recruiter_profile)

    assert invite_recruiters(job, actor=ops, recruiters=[recruiter_profile]) == []
    assert not JobInvite.objects.filter(job=job).exists()
    assert mailoutbox == []
