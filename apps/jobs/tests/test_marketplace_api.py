from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import Channel
from apps.jobs.models import JobAssignment, JobStatus
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db

JOBS = "/api/v1/marketplace/jobs"


def test_marketplace_lists_only_active_jobs_and_filters_search(
    client_for: Callable[[User], APIClient], recruiter_user: User
) -> None:
    JobFactory(title="Senior Backend Engineer", skills=["Go", "Kafka"])
    JobFactory(title="Product Designer", skills=["Figma"])
    JobFactory(title="Head of Finance", status=JobStatus.PENDING)
    client = client_for(recruiter_user)

    assert client.get(JOBS).json()["count"] == 2
    result = client.get(f"{JOBS}?search=kafka").json()
    assert [row["title"] for row in result["results"]] == ["Senior Backend Engineer"]


def test_join_leave_rejoin_keeps_one_assignment_and_channel(
    client_for: Callable[[User], APIClient],
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    job = JobFactory()
    client = client_for(recruiter_user)
    join_url = f"{JOBS}/{job.id}/join"
    leave_url = f"{JOBS}/{job.id}/leave"

    first = client.post(join_url, {}, format="json")
    second = client.post(join_url, {}, format="json")
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json() == second.json()
    assert client.get(f"{JOBS}/{job.id}").json()["joined"] is True

    left = client.post(leave_url, {}, format="json")
    assert left.status_code == 200
    assert left.json()["joined"] is False
    assert client.get(f"{JOBS}/{job.id}").json()["joined"] is False
    assert client.post(join_url, {}, format="json").status_code == 200
    assert JobAssignment.objects.filter(job=job, recruiter=recruiter_profile).count() == 1
    assert Channel.objects.get(job=job, recruiter=recruiter_profile).messages.count() == 1


def test_pending_recruiter_is_refused(
    client_for: Callable[[User], APIClient], pending_recruiter_user: User
) -> None:
    response = client_for(pending_recruiter_user).get(JOBS)
    assert response.status_code == 403
    assert response.json() == {"detail": "Your recruiter profile is waiting for review."}


def test_closed_job_is_hidden_even_if_joined(
    client_for: Callable[[User], APIClient], recruiter_user: User
) -> None:
    job = JobFactory(status=JobStatus.CLOSED)
    client = client_for(recruiter_user)
    assert client.get(f"{JOBS}/{job.id}").status_code == 404
    assert client.post(f"{JOBS}/{job.id}/join", {}, format="json").status_code == 404
