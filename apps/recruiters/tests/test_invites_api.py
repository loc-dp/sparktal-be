"""A headhunter's invites (spec §9): list what is open, accept or decline one, and the overview."""

from collections.abc import Callable
from datetime import timedelta
from typing import cast
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.chat.models import Channel
from apps.jobs.distribution import invite_recruiters
from apps.jobs.models import InviteStatus, Job, JobAssignment, JobInvite, JobStatus
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory, JobInviteFactory
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

INVITES = "/api/v1/recruiters/me/invites"
OVERVIEW = "/api/v1/recruiters/me/overview"
INVITE_CLOSED = "This invite is no longer open."


def _direct_job(**fields: object) -> Job:
    """A live job on the invite channel only."""
    return cast(Job, JobFactory(on_marketplace=False, **fields))


def _age(invite: JobInvite, *, days: int) -> JobInvite:
    """Date the invite `days` ago; `created_at` is set by the database on create."""
    JobInvite.objects.filter(pk=invite.pk).update(created_at=timezone.now() - timedelta(days=days))
    invite.refresh_from_db()
    return invite


def test_list_shows_only_my_open_invites(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    older = _age(
        JobInviteFactory(job=_direct_job(title="Senior Accountant"), recruiter=recruiter_profile),
        days=2,
    )
    newer = _age(
        JobInviteFactory(job=_direct_job(title="Head of Sales"), recruiter=recruiter_profile),
        days=1,
    )
    JobInviteFactory(job=_direct_job(title="Someone Else's"))
    JobInviteFactory(
        job=_direct_job(title="Declined"), recruiter=recruiter_profile, status=InviteStatus.DECLINED
    )
    JobInviteFactory(
        job=_direct_job(title="Cancelled"),
        recruiter=recruiter_profile,
        status=InviteStatus.CANCELLED,
    )
    JobInviteFactory(
        job=_direct_job(title="Closed", status=JobStatus.CLOSED), recruiter=recruiter_profile
    )
    JobInviteFactory(
        job=_direct_job(title="Deleted", deleted_at=timezone.now()), recruiter=recruiter_profile
    )
    JobInviteFactory(
        job=_direct_job(title="No bounty", bounty_type=None, bounty_pct=None),
        recruiter=recruiter_profile,
    )

    response = client_for(recruiter_user).get(INVITES)

    assert response.status_code == 200
    rows = response.json()
    assert isinstance(rows, list)  # not paginated
    assert [(row["id"], row["job"]["title"]) for row in rows] == [
        (str(newer.pk), "Head of Sales"),
        (str(older.pk), "Senior Accountant"),
    ]
    assert set(rows[0]) == {"id", "created_at", "job"}
    job = rows[0]["job"]
    assert job["bounty_type"] == "pct"
    assert job["bounty_pct"] == 18
    assert job["company"]["name"]
    assert job["joined"] is False


def test_reinvited_row_moves_to_the_top(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    # P2-R10: "newest first" follows when the invite was last sent.
    ops = cast(User, UserFactory(is_staff=True))
    first = _age(
        JobInviteFactory(
            job=_direct_job(title="Declined once"),
            recruiter=recruiter_profile,
            status=InviteStatus.DECLINED,
        ),
        days=5,
    )
    second = _age(
        JobInviteFactory(job=_direct_job(title="Still pending"), recruiter=recruiter_profile),
        days=1,
    )
    client = client_for(recruiter_user)
    assert [row["id"] for row in client.get(INVITES).json()] == [str(second.pk)]

    invite_recruiters(first.job, actor=ops, recruiters=[recruiter_profile])

    assert [row["id"] for row in client.get(INVITES).json()] == [str(first.pk), str(second.pk)]


def test_accept_creates_assignment_and_channel(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    invite = JobInviteFactory(job=_direct_job(), recruiter=recruiter_profile)

    response = client_for(recruiter_user).post(f"{INVITES}/{invite.pk}/accept")

    assert response.status_code == 201
    assignment = JobAssignment.objects.get(job=invite.job, recruiter=recruiter_profile)
    assert assignment.left_at is None
    channel = Channel.objects.get(job=invite.job, recruiter=recruiter_profile)
    assert response.json() == {
        "assignment_id": str(assignment.pk),
        "channel_id": str(channel.pk),
        "joined": True,
    }
    invite.refresh_from_db()
    assert invite.status == InviteStatus.ACCEPTED
    assert invite.responded_at is not None
    # It is answered, so it is gone from the open list.
    assert client_for(recruiter_user).get(INVITES).json() == []


@pytest.mark.parametrize("stale", ["job_closed", "job_deleted", "no_bounty", "cancelled"])
def test_accept_stale_invite_is_rejected(
    stale: str,
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    invite = JobInviteFactory(job=_direct_job(), recruiter=recruiter_profile)
    if stale == "cancelled":
        JobInvite.objects.filter(pk=invite.pk).update(status=InviteStatus.CANCELLED)
    elif stale == "no_bounty":
        Job.objects.filter(pk=invite.job_id).update(bounty_type=None, bounty_pct=None)
    elif stale == "job_deleted":
        Job.objects.filter(pk=invite.job_id).update(deleted_at=timezone.now())
    else:
        Job.objects.filter(pk=invite.job_id).update(status=JobStatus.CLOSED)

    response = client_for(recruiter_user).post(f"{INVITES}/{invite.pk}/accept")

    assert response.status_code == 400
    assert response.json()["detail"] == INVITE_CLOSED
    assert not JobAssignment.objects.filter(recruiter=recruiter_profile).exists()
    assert not Channel.objects.filter(recruiter=recruiter_profile).exists()


def test_accept_other_recruiters_invite_is_404(
    client_for: Callable[[User], APIClient], recruiter_user: User
) -> None:
    invite = JobInviteFactory(job=_direct_job())

    response = client_for(recruiter_user).post(f"{INVITES}/{invite.pk}/accept")

    assert response.status_code == 404
    invite.refresh_from_db()
    assert invite.status == InviteStatus.PENDING
    assert not JobAssignment.objects.filter(job=invite.job).exists()


def test_unknown_invite_is_404(
    client_for: Callable[[User], APIClient], recruiter_user: User
) -> None:
    client = client_for(recruiter_user)

    assert client.post(f"{INVITES}/{uuid4()}/accept").status_code == 404
    assert client.post(f"{INVITES}/{uuid4()}/decline", {}, format="json").status_code == 404


def test_decline_stores_reason(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    invite = JobInviteFactory(job=_direct_job(), recruiter=recruiter_profile)

    response = client_for(recruiter_user).post(
        f"{INVITES}/{invite.pk}/decline", {"reason": "  Already full this quarter "}, format="json"
    )

    assert response.status_code == 204
    invite.refresh_from_db()
    assert invite.status == InviteStatus.DECLINED
    assert invite.decline_reason == "Already full this quarter"
    assert invite.responded_at is not None
    assert not JobAssignment.objects.filter(recruiter=recruiter_profile).exists()


def test_decline_without_a_body_is_fine(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    invite = JobInviteFactory(job=_direct_job(), recruiter=recruiter_profile)

    response = client_for(recruiter_user).post(f"{INVITES}/{invite.pk}/decline")

    assert response.status_code == 204
    invite.refresh_from_db()
    assert invite.status == InviteStatus.DECLINED
    assert invite.decline_reason == ""


def test_decline_reason_over_500_is_a_field_error(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    invite = JobInviteFactory(job=_direct_job(), recruiter=recruiter_profile)

    response = client_for(recruiter_user).post(
        f"{INVITES}/{invite.pk}/decline", {"reason": "x" * 501}, format="json"
    )

    assert response.status_code == 400
    assert "reason" in response.json()
    invite.refresh_from_db()
    assert invite.status == InviteStatus.PENDING


def test_decline_other_recruiters_or_answered_invite(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    client = client_for(recruiter_user)
    theirs = JobInviteFactory(job=_direct_job())
    answered = JobInviteFactory(
        job=_direct_job(), recruiter=recruiter_profile, status=InviteStatus.DECLINED
    )

    assert client.post(f"{INVITES}/{theirs.pk}/decline", {}, format="json").status_code == 404
    theirs.refresh_from_db()
    assert theirs.status == InviteStatus.PENDING

    response = client.post(f"{INVITES}/{answered.pk}/decline", {}, format="json")
    assert response.status_code == 400
    assert response.json()["detail"] == INVITE_CLOSED


def test_overview_counts_pending_invites(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    JobInviteFactory(job=_direct_job(), recruiter=recruiter_profile)
    JobInviteFactory(job=_direct_job(), recruiter=recruiter_profile)
    # None of these is open.
    JobInviteFactory(job=_direct_job())
    JobInviteFactory(job=_direct_job(), recruiter=recruiter_profile, status=InviteStatus.DECLINED)
    JobInviteFactory(job=_direct_job(status=JobStatus.CLOSED), recruiter=recruiter_profile)

    payload = client_for(recruiter_user).get(OVERVIEW).json()

    assert payload["pending_invites"] == 2
    assert len(client_for(recruiter_user).get(INVITES).json()) == 2


def test_overview_waiting_jobs_leave_out_jobs_that_are_not_live(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    # P2-R9: submit refuses a closed, deleted or bounty-less job, so it is not "waiting for a CV".
    live = JobFactory(title="Live Job")
    closed = JobFactory(title="Closed Job", status=JobStatus.CLOSED)
    deleted = JobFactory(title="Deleted Job", deleted_at=timezone.now())
    no_bounty = JobFactory(title="No Bounty Job", bounty_type=None, bounty_pct=None)
    for job in (live, closed, deleted, no_bounty):
        JobAssignmentFactory(job=job, recruiter=recruiter_profile)

    payload = client_for(recruiter_user).get(OVERVIEW).json()

    assert payload["jobs_waiting_for_cv"] == 1
    assert [row["title"] for row in payload["waiting_jobs"]] == ["Live Job"]
    # CommissionVault keeps every job they are on.
    rows = client_for(recruiter_user).get("/api/v1/recruiters/me/jobs").json()["results"]
    assert {row["title"] for row in rows} == {
        "Live Job",
        "Closed Job",
        "Deleted Job",
        "No Bounty Job",
    }


@pytest.mark.parametrize(
    ("method", "suffix"), [("get", ""), ("post", "/accept"), ("post", "/decline")]
)
def test_unapproved_recruiter_gets_403(
    method: str,
    suffix: str,
    client_for: Callable[[User], APIClient],
    pending_recruiter_user: User,
) -> None:
    invite = JobInviteFactory(job=_direct_job(), recruiter=RecruiterProfileFactory())
    url = INVITES if method == "get" else f"{INVITES}/{invite.pk}{suffix}"

    response = getattr(client_for(pending_recruiter_user), method)(url)

    assert response.status_code == 403
    invite.refresh_from_db()
    assert invite.status == InviteStatus.PENDING


def test_list_query_count_does_not_grow_with_invites(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
    django_assert_max_num_queries: Callable[..., object],
) -> None:
    for _ in range(5):
        JobInviteFactory(job=_direct_job(), recruiter=recruiter_profile)
    client = client_for(recruiter_user)

    with django_assert_max_num_queries(5):
        response = client.get(INVITES)

    assert len(response.json()) == 5
