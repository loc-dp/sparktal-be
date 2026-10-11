from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory
from apps.companies.models import Company
from apps.jobs.models import JobStatus
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db

ClientFor = Callable[[User], APIClient]


@pytest.fixture
def job_with_pipeline(company: Company) -> Any:
    job = JobFactory(
        company=company,
        title="Senior Backend Engineer",
        status=JobStatus.ACTIVE,
        published_at=timezone.now() - timedelta(days=10),
    )
    ApplicationFactory(job=job, stage=Stage.NEW, candidate__full_name="Bùi Anh Tuấn")
    ApplicationFactory(job=job, stage=Stage.SCREEN)
    ApplicationFactory(job=job, stage=Stage.SCREEN)
    ApplicationFactory(job=job, stage=Stage.INTERVIEW)
    rejected = ApplicationFactory(job=job, stage=Stage.NEW, candidate__full_name="Vũ Khánh Linh")
    rejected.reject("No payments experience")
    return job


def test_overview_returns_kpis(
    client_for: ClientFor, company_ta: User, job_with_pipeline: Any
) -> None:
    response = client_for(company_ta).get(f"/api/v1/jobs/{job_with_pipeline.id}/overview")

    assert response.status_code == 200
    assert response.json()["kpis"] == {
        "days_open": 10,
        "applicants": 5,
        "screen": 2,
        "interviewing": 1,
        "offer": 0,
    }


def test_overview_lists_new_applicants_and_rejections(
    client_for: ClientFor, company_ta: User, job_with_pipeline: Any
) -> None:
    body = client_for(company_ta).get(f"/api/v1/jobs/{job_with_pipeline.id}/overview").json()

    assert [row["candidate_name"] for row in body["new_applicants"]] == ["Bùi Anh Tuấn"]
    assert body["new_applicants"][0]["recruiter_nickname"]
    assert body["rejection_log"][0]["candidate_name"] == "Vũ Khánh Linh"
    assert body["rejection_log"][0]["reason"] == "No payments experience"
    rejected = Application.objects.get(job=job_with_pipeline, stage=Stage.REJECTED)
    assert body["rejection_log"][0]["id"] == str(rejected.id)


def test_overview_groups_recruiters_by_cv_count(
    client_for: ClientFor, company_ta: User, job_with_pipeline: Any
) -> None:
    body = client_for(company_ta).get(f"/api/v1/jobs/{job_with_pipeline.id}/overview").json()

    assert sum(row["cv_count"] for row in body["recruiters"]) == 5
    assert all("nickname" in row and "email" not in row for row in body["recruiters"])


def test_overview_is_404_for_another_company(
    client_for: ClientFor, other_company_admin: User, job_with_pipeline: Any
) -> None:
    response = client_for(other_company_admin).get(f"/api/v1/jobs/{job_with_pipeline.id}/overview")

    assert response.status_code == 404


def test_close_and_reopen(client_for: ClientFor, company_ta: User, job_with_pipeline: Any) -> None:
    client = client_for(company_ta)
    url = f"/api/v1/jobs/{job_with_pipeline.id}"

    closed = client.post(f"{url}/close")

    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    job_with_pipeline.refresh_from_db()
    assert job_with_pipeline.closed_at is not None

    reopened = client.post(f"{url}/reopen")

    assert reopened.json()["status"] == "active"
    job_with_pipeline.refresh_from_db()
    assert job_with_pipeline.closed_at is None


def test_a_pending_job_cannot_be_closed(
    client_for: ClientFor, company_ta: User, company: Company
) -> None:
    job = JobFactory(company=company, status=JobStatus.PENDING, published_at=None)

    response = client_for(company_ta).post(f"/api/v1/jobs/{job.id}/close")

    assert response.status_code == 400
    assert "Only an active job can be closed" in str(response.json())


def test_hiring_manager_without_access_cannot_close(
    client_for: ClientFor, company_hm: User, job_with_pipeline: Any
) -> None:
    response = client_for(company_hm).post(f"/api/v1/jobs/{job_with_pipeline.id}/close")

    assert response.status_code == 404


def test_note_for_recruiters_is_saved_through_patch(
    client_for: ClientFor, company_ta: User, job_with_pipeline: Any
) -> None:
    response = client_for(company_ta).patch(
        f"/api/v1/jobs/{job_with_pipeline.id}",
        {"note_for_recruiters": "Send Go engineers with payments experience only."},
        format="json",
    )

    assert response.status_code == 200
    job_with_pipeline.refresh_from_db()
    assert job_with_pipeline.note_for_recruiters.startswith("Send Go engineers")
