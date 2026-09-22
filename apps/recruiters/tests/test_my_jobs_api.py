from collections.abc import Callable
from typing import cast

import pytest
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
    from django.utils import timezone

    left.left_at = timezone.now()
    left.save(update_fields=["left_at"])
    rows = client_for(recruiter_user).get("/api/v1/recruiters/me/jobs").json()["results"]
    assert {row["title"] for row in rows} == {"Senior Backend Engineer", "QA Engineer"}
    assert next(row for row in rows if row["title"] == "QA Engineer")["status"] == "closed"


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


def test_pending_recruiter_cannot_read_overview(
    client_for: Callable[[User], APIClient], pending_recruiter_user: User
) -> None:
    response = client_for(pending_recruiter_user).get("/api/v1/recruiters/me/overview")

    assert response.status_code == 403
