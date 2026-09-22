import uuid
from typing import Any

import pytest
from django.core.cache import cache
from django.test import override_settings

from apps.accounts.models import User
from apps.candidates.models import Stage
from apps.candidates.tests.factories import ApplicationFactory
from apps.exports.constants import MAX_EXPORT_FILES, RATE_LIMIT_MAX
from apps.exports.models import CvExport
from apps.jobs.access import grant_creator_perms
from apps.jobs.models import Job
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db

LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "create-api",
    }
}


def url(job: Job) -> str:
    return f"/api/v1/jobs/{job.id}/cv-exports"


@pytest.fixture
def three_applications(job: Job, recruiter_profile: Any) -> list[Any]:
    return [
        ApplicationFactory(job=job, recruiter=recruiter_profile, stage=Stage.NEW),
        ApplicationFactory(job=job, recruiter=recruiter_profile, stage=Stage.INTERVIEW),
        ApplicationFactory(job=job, recruiter=recruiter_profile, stage=Stage.REJECTED),
    ]


def test_selected_ids_are_accepted_and_the_job_is_queued(
    client_for: Any, company_ta: User, job: Job, three_applications: list[Any]
) -> None:
    chosen = [str(three_applications[0].id), str(three_applications[1].id)]

    response = client_for(company_ta).post(url(job), {"application_ids": chosen}, format="json")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] in {"queued", "running", "done"}
    assert body["download_url"] is None or body["status"] == "done"
    export = CvExport.objects.get(id=body["id"])
    assert export.requested_by == company_ta
    assert {str(item) for item in export.application_ids} == set(chosen)


def test_an_empty_list_means_every_non_rejected_application(
    client_for: Any, company_ta: User, job: Job, three_applications: list[Any]
) -> None:
    response = client_for(company_ta).post(url(job), {"application_ids": []}, format="json")

    assert response.status_code == 202
    export = CvExport.objects.get(id=response.json()["id"])
    assert export.application_ids == []


def test_a_job_with_no_applicants_is_rejected(client_for: Any, company_ta: User, job: Job) -> None:
    response = client_for(company_ta).post(url(job), {"application_ids": []}, format="json")

    assert response.status_code == 400
    assert "no applicants" in str(response.json()["application_ids"]).lower()


def test_ids_from_another_job_are_rejected(
    client_for: Any, company_ta: User, job: Job, recruiter_profile: Any
) -> None:
    other = ApplicationFactory(job=JobFactory(company=job.company), recruiter=recruiter_profile)

    response = client_for(company_ta).post(
        url(job), {"application_ids": [str(other.id)]}, format="json"
    )

    assert response.status_code == 400
    assert str(other.id) in str(response.json()["application_ids"])


def test_unknown_ids_are_rejected(client_for: Any, company_ta: User, job: Job) -> None:
    response = client_for(company_ta).post(
        url(job), {"application_ids": [str(uuid.uuid4())]}, format="json"
    )

    assert response.status_code == 400


def test_more_than_200_files_is_rejected(
    client_for: Any, company_ta: User, job: Job, recruiter_profile: Any
) -> None:
    ids = [str(uuid.uuid4()) for _ in range(MAX_EXPORT_FILES + 1)]

    response = client_for(company_ta).post(url(job), {"application_ids": ids}, format="json")

    assert response.status_code == 400
    assert f"at most {MAX_EXPORT_FILES}" in str(response.json()["application_ids"])


@override_settings(CACHES=LOCMEM)
def test_the_sixth_export_in_ten_minutes_is_429(
    client_for: Any, company_ta: User, job: Job, three_applications: list[Any]
) -> None:
    cache.clear()
    client = client_for(company_ta)
    body = {"application_ids": [str(three_applications[0].id)]}

    for _ in range(RATE_LIMIT_MAX):
        assert client.post(url(job), body, format="json").status_code == 202

    response = client.post(url(job), body, format="json")

    assert response.status_code == 429
    assert "Retry-After" in response
    assert str(RATE_LIMIT_MAX) in response.json()["detail"]


def test_permission_matrix(
    client_for: Any,
    api_client: Any,
    job: Job,
    company_admin: User,
    company_hm: User,
    other_company_admin: User,
    recruiter_user: User,
    three_applications: list[Any],
) -> None:
    body = {"application_ids": [str(three_applications[0].id)]}

    assert client_for(company_admin).post(url(job), body, format="json").status_code == 202
    assert client_for(company_hm).post(url(job), body, format="json").status_code == 404
    grant_creator_perms(job, company_hm)
    assert client_for(company_hm).post(url(job), body, format="json").status_code == 202
    assert client_for(other_company_admin).post(url(job), body, format="json").status_code == 404
    assert client_for(recruiter_user).post(url(job), body, format="json").status_code == 404
    assert api_client.post(url(job), body, format="json").status_code == 403
