"""Sparktal ops hear about every job entering the queue and every edit to a live job (spec §10);
a headhunter hears when Sparktal assigns them a job."""

import logging
from collections.abc import Callable
from typing import Any, cast

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.jobs.distribution import assign_recruiters
from apps.jobs.models import BountyType, ChangeKind, Job, JobAssignment, JobStatus
from apps.jobs.notifications import ops_job_url
from apps.jobs.proposals import approve_change, propose_change
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

ClientFor = Callable[[User], APIClient]
Commit = Callable[..., Any]

BRIEF = {"role_type": "new_position", "working_hours": "40h_mon_fri", "languages": ["en"]}


def _draft(world: TeamWorld, **fields: Any) -> Job:
    values: dict[str, Any] = {
        "company": world.company,
        "team": world.team_a,
        "created_by": world.ta_publish,
        "status": JobStatus.DRAFT,
        "published_at": None,
        "title": "Payments SRE",
        **BRIEF,
    }
    return cast(Job, JobFactory(**{**values, **fields}))


def _html(message: Any) -> str:
    return str(message.alternatives[0][0])


def test_post_emails_ops_once(
    team_world: TeamWorld,
    client_for: ClientFor,
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Commit,
) -> None:
    job = _draft(team_world)

    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(team_world.ta_publish).patch(
            f"/api/v1/jobs/{job.pk}", {"title": "Payments SRE", "intent": "post"}, format="json"
        )

    assert response.status_code == 200
    assert response.data["status"] == JobStatus.PENDING
    assert len(mailoutbox) == 1
    message = mailoutbox[0]
    assert message.to == ["ops@test.local"]
    assert message.subject == "New job to distribute: Payments SRE · Orbit Pay"
    link = f"/a/jobs/{job.pk}"
    assert link in message.body
    assert link in _html(message)
    for text in (message.body, _html(message)):
        assert "Set the bounty and pick at least one headhunter to put it live." in text
        assert "marketplace" not in text.lower()
    job.refresh_from_db()
    assert job.posted_at is not None


def test_ops_email_links_to_portal(
    team_world: TeamWorld,
    client_for: ClientFor,
    mailoutbox: list[Any],
    settings: Any,
    django_capture_on_commit_callbacks: Commit,
) -> None:
    """Both ops emails open the job in the ops portal at `{FRONTEND_URL}/a/jobs/{id}`."""
    settings.FRONTEND_URL = "https://app.sparktal.test"
    job = _draft(team_world)
    portal = f"https://app.sparktal.test/a/jobs/{job.pk}"

    with django_capture_on_commit_callbacks(execute=True):
        queued = client_for(team_world.ta_publish).post(f"/api/v1/jobs/{job.pk}/post")
    (queued_mail,) = mailoutbox
    mailoutbox.clear()
    live = team_world.job_a
    with django_capture_on_commit_callbacks(execute=True):
        edited = client_for(team_world.admin).patch(
            f"/api/v1/jobs/{live.pk}", {"title": "Payments Platform Engineer"}, format="json"
        )
    (edited_mail,) = mailoutbox

    assert queued.status_code == 200
    assert edited.status_code == 200
    assert ops_job_url(job) == portal
    for message, url in (
        (queued_mail, portal),
        (edited_mail, f"https://app.sparktal.test/a/jobs/{live.pk}"),
    ):
        assert url in message.body
        assert url in _html(message)
        assert "/admin/" not in message.body
        assert "/admin/" not in _html(message)


def test_edit_of_active_job_emails_ops_with_changed_fields(
    team_world: TeamWorld,
    client_for: ClientFor,
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Commit,
) -> None:
    job = team_world.job_a
    admin = client_for(team_world.admin)

    with django_capture_on_commit_callbacks(execute=True):
        response = admin.patch(
            f"/api/v1/jobs/{job.pk}",
            {"title": "Payments Platform Engineer", "roles": job.roles, "salary_max": 70_000_000},
            format="json",
        )

    assert response.status_code == 200
    assert len(mailoutbox) == 1
    message = mailoutbox[0]
    assert message.to == ["ops@test.local"]
    assert message.subject == "Live job edited: Payments Platform Engineer · Orbit Pay"
    assert "Title" in message.body
    assert "Salary max" in message.body
    assert "Roles" not in message.body
    assert "Salary max" in _html(message)
    assert f"/a/jobs/{job.pk}" in message.body

    mailoutbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        unchanged = admin.patch(
            f"/api/v1/jobs/{job.pk}", {"title": "Payments Platform Engineer"}, format="json"
        )

    assert unchanged.status_code == 200
    assert mailoutbox == []


def test_edit_of_pending_job_sends_nothing(
    team_world: TeamWorld,
    client_for: ClientFor,
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Commit,
) -> None:
    job = _draft(team_world, status=JobStatus.PENDING)

    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(team_world.admin).patch(
            f"/api/v1/jobs/{job.pk}", {"title": "Payments SRE II"}, format="json"
        )

    assert response.status_code == 200
    assert mailoutbox == []


def test_approved_edit_proposal_on_active_job_emails_ops(
    team_world: TeamWorld, mailoutbox: list[Any], django_capture_on_commit_callbacks: Commit
) -> None:
    job = cast(
        Job,
        JobFactory(
            company=team_world.company,
            team=team_world.team_a,
            created_by=team_world.ta_draft,
            title="Payments Analyst",
            **BRIEF,
        ),
    )
    request = propose_change(
        job, actor=team_world.ta_draft, kind=ChangeKind.EDIT, changes={"title": "Senior Analyst"}
    )
    mailoutbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        approve_change(request, actor=team_world.admin)

    to_ops = [message for message in mailoutbox if message.to == ["ops@test.local"]]
    assert len(to_ops) == 1
    assert to_ops[0].subject == "Live job edited: Senior Analyst · Orbit Pay"
    assert "Title" in to_ops[0].body


def test_ops_email_failure_does_not_block_post(
    team_world: TeamWorld,
    client_for: ClientFor,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: Commit,
) -> None:
    def boom(self: Any, *args: Any, **kwargs: Any) -> int:
        raise OSError("smtp down")

    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", boom)
    job = _draft(team_world)

    with caplog.at_level(logging.ERROR), django_capture_on_commit_callbacks(execute=True):
        response = client_for(team_world.ta_publish).post(f"/api/v1/jobs/{job.pk}/post")

    assert response.status_code == 200
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING
    assert "Could not send email" in caplog.text
    assert "New job to distribute" in caplog.text


def test_ops_email_failure_does_not_block_an_edit(
    team_world: TeamWorld,
    client_for: ClientFor,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: Commit,
) -> None:
    def boom(self: Any, *args: Any, **kwargs: Any) -> int:
        raise OSError("smtp down")

    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", boom)
    job = team_world.job_a

    with caplog.at_level(logging.ERROR), django_capture_on_commit_callbacks(execute=True):
        response = client_for(team_world.admin).patch(
            f"/api/v1/jobs/{job.pk}", {"title": "Payments Platform Engineer"}, format="json"
        )

    assert response.status_code == 200
    job.refresh_from_db()
    assert job.title == "Payments Platform Engineer"
    assert job.status == JobStatus.ACTIVE
    assert "Could not send email" in caplog.text
    assert "Live job edited" in caplog.text


def _staff() -> User:
    return cast(User, UserFactory(email="long.mai@sparktal.com", is_staff=True))


def _headhunter() -> RecruiterProfile:
    return cast(
        RecruiterProfile,
        RecruiterProfileFactory(nickname="Bluefin", user__email="bluefin@example.com"),
    )


def test_assigned_email_names_the_job_and_its_fixed_bounty(
    team_world: TeamWorld,
    mailoutbox: list[Any],
    settings: Any,
    django_capture_on_commit_callbacks: Commit,
) -> None:
    settings.FRONTEND_URL = "https://app.sparktal.test"
    job = cast(
        Job,
        JobFactory(
            company=team_world.company,
            title="R&D Lead",
            bounty_type=BountyType.FIXED,
            bounty_pct=None,
            bounty_amount=120_000_000,
        ),
    )

    with django_capture_on_commit_callbacks(execute=True):
        assign_recruiters(job, actor=_staff(), recruiters=[_headhunter()])

    (message,) = mailoutbox
    assert message.to == ["bluefin@example.com"]
    assert message.subject == "You have been assigned a job: R&D Lead"
    for body in (message.body, _html(message)):
        assert "Sparktal assigned you" in body
        assert "Orbit Pay" in body
        assert "Fixed amount · 120.000.000 ₫" in body
        assert f"https://app.sparktal.test/r/jobs/{job.pk}" in body
        assert "accept" not in body.lower()
    assert "Open the job" in _html(message)


def test_assigned_email_failure_does_not_block_the_assignment(
    team_world: TeamWorld,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: Commit,
) -> None:
    def boom(self: Any, *args: Any, **kwargs: Any) -> int:
        raise OSError("smtp down")

    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", boom)
    headhunter = _headhunter()

    with caplog.at_level(logging.ERROR), django_capture_on_commit_callbacks(execute=True):
        assign_recruiters(team_world.job_a, actor=_staff(), recruiters=[headhunter])

    assert JobAssignment.objects.filter(
        job=team_world.job_a, recruiter=headhunter, left_at__isnull=True
    ).exists()
    assert "Could not send email" in caplog.text
    assert "You have been assigned a job" in caplog.text
