import logging
from collections.abc import Callable
from typing import Any, cast

import pytest
from django.http import Http404
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.candidates.models import Stage
from apps.candidates.tests.factories import ApplicationFactory
from apps.jobs.access import NOT_YOURS
from apps.jobs.lifecycle import (
    JobRuleError,
    close_job,
    delete_job,
    missing_for_posting,
    post_job,
    reopen_job,
    restore_job,
    submit_job,
    withdraw_job,
)
from apps.jobs.models import InviteStatus, Job, JobStatus
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory, JobInviteFactory

pytestmark = pytest.mark.django_db

DELETE_BLOCKED = "Close the job and finish or archive every candidate before deleting it."


def _complete(world: TeamWorld, **fields: Any) -> Job:
    values: dict[str, Any] = {
        "company": world.company,
        "team": world.team_a,
        "created_by": world.ta_draft,
        "status": JobStatus.DRAFT,
        "published_at": None,
        "role_type": "new_position",
        "working_hours": "40h_mon_fri",
        "languages": ["en"],
    }
    return cast(Job, JobFactory(**{**values, **fields}))


def test_submit_requires_complete_jd(team_world: TeamWorld) -> None:
    job = _complete(team_world, roles="")

    with pytest.raises(ValidationError) as error:
        submit_job(job, actor=team_world.ta_draft)

    assert "roles" in error.value.detail
    job.refresh_from_db()
    assert job.status == JobStatus.DRAFT


def test_submit_moves_draft_to_submitted_and_withdraw_returns_it(team_world: TeamWorld) -> None:
    job = _complete(team_world)

    submit_job(job, actor=team_world.ta_draft)
    job.refresh_from_db()
    assert job.status == JobStatus.SUBMITTED

    withdraw_job(job, actor=team_world.ta_draft)
    job.refresh_from_db()
    assert job.status == JobStatus.DRAFT


def test_submit_emails_every_admin(
    team_world: TeamWorld,
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    from apps.companies.tests.factories import CompanyMembershipFactory

    second = CompanyMembershipFactory(
        company=team_world.company, role="admin", team=None, user__email="admin2@orbit.vn"
    )
    inactive = CompanyMembershipFactory(
        company=team_world.company, role="admin", team=None, user__email="gone@orbit.vn"
    )
    inactive.user.is_active = False
    inactive.user.save()
    job = _complete(team_world, title="Payments SRE")

    with django_capture_on_commit_callbacks(execute=True):
        submit_job(job, actor=team_world.ta_draft)

    recipients = sorted(mail.to[0] for mail in mailoutbox)
    assert recipients == sorted(["admin@orbit.vn", second.user.email])
    assert mailoutbox[0].subject == "Payments SRE is waiting for you to post"
    assert f"/jobs/{job.pk}/edit" in mailoutbox[0].body


def test_email_failure_does_not_break_submit(
    team_world: TeamWorld,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    def boom(self: Any, *args: Any, **kwargs: Any) -> int:
        raise OSError("smtp down")

    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", boom)
    job = _complete(team_world)

    with caplog.at_level(logging.ERROR), django_capture_on_commit_callbacks(execute=True):
        submit_job(job, actor=team_world.ta_draft)

    job.refresh_from_db()
    assert job.status == JobStatus.SUBMITTED
    assert "Could not send email" in caplog.text


def test_post_from_submitted_by_admin(team_world: TeamWorld) -> None:
    job = _complete(team_world, status=JobStatus.SUBMITTED)

    post_job(job, actor=team_world.admin)

    job.refresh_from_db()
    assert job.status == JobStatus.PENDING


def test_ta_with_publish_posts_a_draft_and_a_submitted_job(team_world: TeamWorld) -> None:
    for status in (JobStatus.DRAFT, JobStatus.SUBMITTED):
        job = _complete(team_world, created_by=team_world.ta_publish, status=status)
        post_job(job, actor=team_world.ta_publish)
        job.refresh_from_db()
        assert job.status == JobStatus.PENDING


def test_post_needs_the_complete_jd(team_world: TeamWorld) -> None:
    job = _complete(team_world, created_by=team_world.ta_publish, working_hours="")

    with pytest.raises(ValidationError) as error:
        post_job(job, actor=team_world.ta_publish)

    assert "working_hours" in error.value.detail
    assert "working_hours" in missing_for_posting(job)


def test_ta_without_publish_cannot_post(team_world: TeamWorld) -> None:
    job = _complete(team_world)

    with pytest.raises(PermissionDenied) as error:
        post_job(job, actor=team_world.ta_draft)

    assert str(error.value.detail) == NOT_YOURS


def test_job_the_actor_cannot_see_is_a_404(team_world: TeamWorld) -> None:
    job = _complete(team_world)  # a draft: only its creator sees it

    with pytest.raises(Http404):
        submit_job(job, actor=team_world.hm_b)
    with pytest.raises(Http404):
        post_job(job, actor=team_world.ta_publish)


def test_close_and_reopen(team_world: TeamWorld) -> None:
    job = team_world.job_a

    close_job(job, actor=team_world.ta_publish)
    job.refresh_from_db()
    assert job.status == JobStatus.CLOSED
    assert job.closed_at is not None

    reopen_job(job, actor=team_world.ta_publish)
    job.refresh_from_db()
    assert job.status == JobStatus.ACTIVE
    assert job.closed_at is None


def test_close_is_403_for_ta_without_publish_on_own_submitted_job(team_world: TeamWorld) -> None:
    job = _complete(team_world, status=JobStatus.SUBMITTED)

    with pytest.raises(PermissionDenied) as error:
        close_job(job, actor=team_world.ta_draft)
    with pytest.raises(PermissionDenied):
        reopen_job(job, actor=team_world.ta_draft)

    assert str(error.value.detail) == NOT_YOURS


def test_delete_blocked_by_live_application(team_world: TeamWorld) -> None:
    job = team_world.job_a  # active, with a candidate at stage "new"
    close_job(job, actor=team_world.admin)

    with pytest.raises(JobRuleError) as error:
        delete_job(job, actor=team_world.admin)

    assert str(error.value.detail) == DELETE_BLOCKED
    job.refresh_from_db()
    assert job.deleted_at is None


def test_delete_then_restore(team_world: TeamWorld) -> None:
    job = _complete(team_world)

    delete_job(job, actor=team_world.ta_draft)
    job.refresh_from_db()
    assert job.deleted_at is not None

    restore_job(job)
    job.refresh_from_db()
    assert job.deleted_at is None
    assert job.history.first().deleted_at is None


def test_closed_job_whose_candidates_are_done_can_be_deleted(team_world: TeamWorld) -> None:
    job = team_world.job_a
    team_world.application_a.stage = Stage.REJECTED
    team_world.application_a.save()
    ApplicationFactory(job=job, stage=Stage.ONBOARDED)
    close_job(job, actor=team_world.admin)

    delete_job(job, actor=team_world.admin)

    job.refresh_from_db()
    assert job.deleted_at is not None


def test_post_marks_when_the_job_entered_the_queue(team_world: TeamWorld) -> None:
    job = _complete(team_world, created_by=team_world.ta_publish, posted_at=None)

    post_job(job, actor=team_world.ta_publish)

    job.refresh_from_db()
    assert job.posted_at is not None
    assert job.published_at is None


def test_close_cancels_pending_invites(team_world: TeamWorld) -> None:
    job = team_world.job_a
    pending = JobInviteFactory(job=job)
    accepted = JobInviteFactory(job=job, status=InviteStatus.ACCEPTED)

    close_job(job, actor=team_world.ta_publish)

    pending.refresh_from_db()
    accepted.refresh_from_db()
    assert pending.status == InviteStatus.CANCELLED
    assert accepted.status == InviteStatus.ACCEPTED


@pytest.mark.parametrize("on_marketplace", [True, False])
def test_reopen_keeps_marketplace_flag(team_world: TeamWorld, on_marketplace: bool) -> None:
    job = team_world.job_a
    Job.objects.filter(pk=job.pk).update(on_marketplace=on_marketplace)
    assignment = JobAssignmentFactory(job=job)
    close_job(job, actor=team_world.ta_publish)

    reopen_job(job, actor=team_world.ta_publish)

    job.refresh_from_db()
    assert job.status == JobStatus.ACTIVE
    assert job.on_marketplace is on_marketplace
    assignment.refresh_from_db()
    assert assignment.is_active


def test_reopen_without_bounty_returns_to_queue(
    team_world: TeamWorld,
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Callable[..., Any],
) -> None:
    job = team_world.job_a
    Job.objects.filter(pk=job.pk).update(on_marketplace=False)  # direct-only before it closed
    assignment = JobAssignmentFactory(job=job)
    accepted = JobInviteFactory(job=job, recruiter=assignment.recruiter, status="accepted")
    close_job(job, actor=team_world.ta_publish)
    Job.objects.filter(pk=job.pk).update(bounty_type=None, bounty_pct=None, bounty_amount=None)

    with django_capture_on_commit_callbacks(execute=True):
        reopen_job(job, actor=team_world.ta_publish)

    job.refresh_from_db()
    assert job.status == JobStatus.PENDING
    assert job.closed_at is None
    assert job.on_marketplace is False
    assignment.refresh_from_db()
    accepted.refresh_from_db()
    assert assignment.is_active
    assert accepted.status == InviteStatus.ACCEPTED
    assert [message.to for message in mailoutbox] == [["ops@test.local"]]
    assert mailoutbox[0].subject == "New job to distribute: Payments Backend Engineer · Orbit Pay"
