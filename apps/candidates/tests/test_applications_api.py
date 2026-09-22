from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.models import Stage
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.jobs.access import grant_creator_perms
from apps.jobs.models import Job
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db


def url(job: Job) -> str:
    return f"/api/v1/jobs/{job.id}/applications"


@pytest.fixture
def applications(job: Job, recruiter_profile: Any) -> list[Any]:
    return [
        ApplicationFactory(
            job=job,
            recruiter=recruiter_profile,
            stage=Stage.NEW,
            candidate=CandidateFactory(
                owner=recruiter_profile,
                full_name="Nguyễn Thị Hằng",
                current_title="Backend Engineer",
            ),
            cv_filename="nguyen-thi-hang.pdf",
        ),
        ApplicationFactory(
            job=job,
            recruiter=recruiter_profile,
            stage=Stage.INTERVIEW,
            is_duplicate=True,
            candidate=CandidateFactory(
                owner=recruiter_profile,
                full_name="Trần Minh Khoa",
                current_title="Staff Engineer",
            ),
            cv_filename="tran-minh-khoa.pdf",
        ),
    ]


def test_list_returns_the_contract_fields_newest_first(
    client_for: Any, company_ta: User, job: Job, applications: list[Any]
) -> None:
    response = client_for(company_ta).get(url(job))

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    row = body["results"][0]
    assert set(row) == {
        "id",
        "candidate",
        "recruiter",
        "stage",
        "submitted_at",
        "stage_changed_at",
        "is_duplicate",
        "cv_filename",
    }
    assert set(row["candidate"]) == {"full_name", "current_title"}
    assert set(row["recruiter"]) == {"id", "nickname"}
    submitted = [item["submitted_at"] for item in body["results"]]
    assert submitted == sorted(submitted, reverse=True)


def test_recruiter_email_is_never_exposed(
    client_for: Any, company_ta: User, job: Job, applications: list[Any]
) -> None:
    body = client_for(company_ta).get(url(job)).json()

    assert "email" not in str(body)


def test_stage_filter(client_for: Any, company_ta: User, job: Job, applications: list[Any]) -> None:
    body = client_for(company_ta).get(f"{url(job)}?stage=interview").json()

    assert body["count"] == 1
    assert body["results"][0]["candidate"]["full_name"] == "Trần Minh Khoa"


def test_search_matches_name_and_current_title(
    client_for: Any, company_ta: User, job: Job, applications: list[Any]
) -> None:
    client = client_for(company_ta)

    assert client.get(f"{url(job)}?search=hằng").json()["count"] == 1
    assert client.get(f"{url(job)}?search=staff").json()["count"] == 1
    assert client.get(f"{url(job)}?search=engineer").json()["count"] == 2
    assert client.get(f"{url(job)}?search=nobody").json()["count"] == 0


def test_applications_of_another_job_are_not_listed(
    client_for: Any,
    company_ta: User,
    job: Job,
    applications: list[Any],
    recruiter_profile: Any,
) -> None:
    other = JobFactory(company=job.company, title="Data Analyst")
    ApplicationFactory(job=other, recruiter=recruiter_profile)

    assert client_for(company_ta).get(url(job)).json()["count"] == 2


@pytest.mark.parametrize("stage", ["nope", "NEW"])
def test_an_unknown_stage_is_rejected(
    client_for: Any, company_ta: User, job: Job, stage: str
) -> None:
    assert client_for(company_ta).get(f"{url(job)}?stage={stage}").status_code == 400


def test_permission_matrix(
    client_for: Any,
    api_client: APIClient,
    job: Job,
    company_admin: User,
    company_ta: User,
    company_hm: User,
    other_company_admin: User,
    recruiter_user: User,
    applications: list[Any],
) -> None:
    assert client_for(company_admin).get(url(job)).status_code == 200
    assert client_for(company_ta).get(url(job)).status_code == 200
    assert client_for(company_hm).get(url(job)).status_code == 404
    grant_creator_perms(job, company_hm)
    assert client_for(company_hm).get(url(job)).status_code == 200
    assert client_for(other_company_admin).get(url(job)).status_code == 404
    assert client_for(recruiter_user).get(url(job)).status_code == 404
    assert api_client.get(url(job)).status_code == 403
