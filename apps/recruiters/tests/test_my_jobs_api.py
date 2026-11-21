from collections.abc import Callable
from datetime import timedelta
from typing import cast

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.jobs.models import Job, JobStatus
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db


def submit(job: Job, recruiter: RecruiterProfile, stage: str = Stage.NEW) -> Application:
    return cast(
        Application,
        ApplicationFactory(
            job=job, candidate=CandidateFactory(owner=recruiter), recruiter=recruiter, stage=stage
        ),
    )


def test_my_jobs_only_lists_active_assignments_and_keeps_closed_jobs(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    mine = JobFactory(title="Senior Backend Engineer")
    JobAssignmentFactory(job=mine, recruiter=recruiter_profile)
    closed = JobFactory(title="QA Engineer", status=JobStatus.CLOSED)
    JobAssignmentFactory(job=closed, recruiter=recruiter_profile)
    JobAssignmentFactory(job=JobFactory(title="Other Recruiter Job"))
    left = JobAssignmentFactory(job=JobFactory(title="Left Job"), recruiter=recruiter_profile)
    left.left_at = timezone.now()
    left.save(update_fields=["left_at"])
    rows = client_for(recruiter_user).get("/api/v1/recruiters/me/jobs").json()["results"]
    assert {row["title"] for row in rows} == {"Senior Backend Engineer", "QA Engineer"}
    assert next(row for row in rows if row["title"] == "QA Engineer")["status"] == "closed"


def test_list_includes_removed_job_with_cv_flagged(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    held = JobFactory(title="Held Job")
    JobAssignmentFactory(job=held, recruiter=recruiter_profile)
    removed = JobFactory(title="Removed With CV")
    removal = JobAssignmentFactory(job=removed, recruiter=recruiter_profile)
    submit(removed, recruiter_profile, Stage.INTERVIEW)
    removal.left_at = timezone.now()
    removal.save(update_fields=["left_at"])
    gone = JobFactory(title="Removed Without CV")
    gone_removal = JobAssignmentFactory(job=gone, recruiter=recruiter_profile)
    gone_removal.left_at = timezone.now()
    gone_removal.save(update_fields=["left_at"])

    rows = client_for(recruiter_user).get("/api/v1/recruiters/me/jobs").json()["results"]

    assert [(row["title"], row["assigned"]) for row in rows] == [
        ("Held Job", True),
        ("Removed With CV", False),
    ]
    assert rows[1]["joined_at"] is None
    assert rows[1]["submitted_count"] == 1


def test_list_puts_held_jobs_first_then_by_last_activity(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    quiet = JobFactory(title="Held, no CV")
    JobAssignmentFactory(job=quiet, recruiter=recruiter_profile)
    busy = JobFactory(title="Held, CV in play")
    JobAssignmentFactory(job=busy, recruiter=recruiter_profile)
    application = submit(busy, recruiter_profile, Stage.SCREEN)
    removed = JobFactory(title="Removed")
    removal = JobAssignmentFactory(job=removed, recruiter=recruiter_profile)
    recent = submit(removed, recruiter_profile, Stage.OFFER)
    removal.left_at = timezone.now()
    removal.save(update_fields=["left_at"])
    # The removed job moved most recently, yet it still sorts after the held ones.
    Application.objects.filter(pk=application.pk).update(
        stage_changed_at=timezone.now() - timedelta(days=3)
    )
    Application.objects.filter(pk=recent.pk).update(stage_changed_at=timezone.now())

    rows = client_for(recruiter_user).get("/api/v1/recruiters/me/jobs").json()["results"]

    assert [row["title"] for row in rows] == ["Held, no CV", "Held, CV in play", "Removed"]


def test_my_jobs_carries_the_note_for_recruiters(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    job = JobFactory(note_for_recruiters="No candidates from MoMo, please.")
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    rows = client_for(recruiter_user).get("/api/v1/recruiters/me/jobs").json()["results"]
    assert rows[0]["note_for_recruiters"] == "No candidates from MoMo, please."


def test_overview_counts_recruiter_stages_and_waiting_jobs(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    joined = JobFactory(title="Senior Backend Engineer")
    waiting = JobFactory(title="Compliance Manager")
    JobAssignmentFactory(job=joined, recruiter=recruiter_profile)
    JobAssignmentFactory(job=waiting, recruiter=recruiter_profile)
    submit(joined, recruiter_profile, Stage.NEW)
    submit(joined, recruiter_profile, Stage.SCREEN)
    submit(joined, recruiter_profile, Stage.INTERVIEW)
    submit(joined, recruiter_profile, Stage.OFFER)
    submit(joined, recruiter_profile, Stage.ONBOARDED)
    payload = client_for(recruiter_user).get("/api/v1/recruiters/me/overview").json()
    assert payload["current_jobs"] == 2
    assert payload["jobs_waiting_for_cv"] == 1
    assert payload["cv_sent"] == 5
    assert payload["cv_screened"] == 4
    assert payload["cv_in_interviews"] == 1
    assert payload["offer"] == 1
    assert [row["title"] for row in payload["waiting_jobs"]] == ["Compliance Manager"]


def test_overview_has_no_marketplace_or_invite_counts(
    client_for: Callable[[User], APIClient], recruiter_user: User
) -> None:
    payload = client_for(recruiter_user).get("/api/v1/recruiters/me/overview").json()

    assert "marketplace_open_count" not in payload
    assert "pending_invites" not in payload


def test_removed_job_with_cv_is_not_a_current_job_in_the_overview(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    removed = JobFactory()
    removal = JobAssignmentFactory(job=removed, recruiter=recruiter_profile)
    submit(removed, recruiter_profile)
    removal.left_at = timezone.now()
    removal.save(update_fields=["left_at"])

    payload = client_for(recruiter_user).get("/api/v1/recruiters/me/overview").json()

    assert (payload["current_jobs"], payload["jobs_waiting_for_cv"]) == (0, 0)
    assert payload["cv_sent"] == 1


def test_pending_recruiter_cannot_read_overview(
    client_for: Callable[[User], APIClient], pending_recruiter_user: User
) -> None:
    response = client_for(pending_recruiter_user).get("/api/v1/recruiters/me/overview")

    assert response.status_code == 403
