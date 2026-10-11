from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.companies.tests.factories import CompanyFactory
from apps.jobs.access import grant_creator_perms
from apps.jobs.models import Job
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

POOL = "/api/v1/company/talent-pool"

ClientFor = Any


def submit(job: Job, recruiter: RecruiterProfile, name: str, **extra: Any) -> Application:
    candidate = extra.pop("candidate", None) or CandidateFactory(
        owner=recruiter, full_name=name, current_title="Backend Engineer"
    )
    application: Application = ApplicationFactory(
        job=job, recruiter=recruiter, candidate=candidate, **extra
    )
    return application


def ago(days: int) -> Any:
    return timezone.now() - timedelta(days=days)


def test_lists_each_candidate_once_with_every_job_they_reached(
    client_for: ClientFor, company_ta: User, job: Job, recruiter_profile: RecruiterProfile
) -> None:
    second_job = JobFactory(company=job.company, title="Data Analyst")
    first = submit(job, recruiter_profile, "Nguyễn Thị Hằng", submitted_at=ago(3))
    submit(
        second_job,
        recruiter_profile,
        "",
        candidate=first.candidate,
        stage=Stage.INTERVIEW,
        submitted_at=ago(1),
    )

    response = client_for(company_ta).get(POOL)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    entry = body["results"][0]
    assert entry["full_name"] == "Nguyễn Thị Hằng"
    assert [a["job"]["title"] for a in entry["applications"]] == ["Data Analyst", job.title]
    assert entry["applications"][0]["stage"] == "interview"
    assert entry["applications"][0]["recruiter"]["nickname"] == recruiter_profile.nickname


def test_never_exposes_contact_details_or_the_recruiters_private_notes(
    client_for: ClientFor, company_ta: User, job: Job, recruiter_profile: RecruiterProfile
) -> None:
    candidate = CandidateFactory(
        owner=recruiter_profile, email="hang@example.com", notes="Wants remote only"
    )
    submit(job, recruiter_profile, "", candidate=candidate)

    entry = client_for(company_ta).get(POOL).json()["results"][0]

    assert set(entry) == {
        "id",
        "full_name",
        "current_title",
        "location",
        "years_experience",
        "skills",
        "last_submitted_at",
        "applications",
    }
    assert set(entry["applications"][0]) == {
        "id",
        "job",
        "recruiter",
        "stage",
        "submitted_at",
        "stage_changed_at",
        "cv_filename",
        "is_duplicate",
    }


def test_the_same_person_from_two_recruiters_is_one_entry(
    client_for: ClientFor, company_ta: User, job: Job, recruiter_profile: RecruiterProfile
) -> None:
    rival = RecruiterProfileFactory(nickname="Bluefin Search")
    second_job = JobFactory(company=job.company)
    original = submit(job, recruiter_profile, "Trần Minh Khoa", submitted_at=ago(5))
    submit(
        second_job,
        rival,
        "Trần Minh Khoa",
        is_duplicate=True,
        duplicate_of=original,
        submitted_at=ago(2),
    )

    body = client_for(company_ta).get(POOL).json()

    assert body["count"] == 1
    recruiters = {a["recruiter"]["nickname"] for a in body["results"][0]["applications"]}
    assert recruiters == {recruiter_profile.nickname, "Bluefin Search"}


def test_most_recently_submitted_comes_first(
    client_for: ClientFor, company_ta: User, job: Job, recruiter_profile: RecruiterProfile
) -> None:
    submit(job, recruiter_profile, "Older", submitted_at=ago(9))
    submit(job, recruiter_profile, "Newer", submitted_at=ago(1))

    names = [e["full_name"] for e in client_for(company_ta).get(POOL).json()["results"]]

    assert names == ["Newer", "Older"]


def test_search_stage_and_job_filters(
    client_for: ClientFor, company_ta: User, job: Job, recruiter_profile: RecruiterProfile
) -> None:
    other_job = JobFactory(company=job.company)
    submit(job, recruiter_profile, "Lê Văn An", stage=Stage.SCREEN)
    submit(other_job, recruiter_profile, "Phạm Thu Hà", stage=Stage.OFFER)
    client = client_for(company_ta)

    def names(query: str) -> list[str]:
        return [e["full_name"] for e in client.get(f"{POOL}?{query}").json()["results"]]

    assert names("search=thu h") == ["Phạm Thu Hà"]
    assert names("stage=screen") == ["Lê Văn An"]
    assert names(f"job={other_job.pk}") == ["Phạm Thu Hà"]
    assert client.get(f"{POOL}?stage=nope").status_code == 400
    assert client.get(f"{POOL}?job=nope").status_code == 400


def test_another_companys_candidates_never_show(
    client_for: ClientFor, company_ta: User, job: Job, recruiter_profile: RecruiterProfile
) -> None:
    elsewhere = JobFactory(company=CompanyFactory(name="Kestrel"))
    submit(elsewhere, recruiter_profile, "Elsewhere")

    assert client_for(company_ta).get(POOL).json()["count"] == 0


def test_a_hiring_manager_sees_only_jobs_granted_to_them(
    client_for: ClientFor,
    company_hm: User,
    job: Job,
    recruiter_profile: RecruiterProfile,
) -> None:
    granted = JobFactory(company=job.company)
    grant_creator_perms(granted, company_hm)
    submit(job, recruiter_profile, "Hidden")
    submit(granted, recruiter_profile, "Visible")

    names = [e["full_name"] for e in client_for(company_hm).get(POOL).json()["results"]]

    assert names == ["Visible"]


def test_a_recruiter_gets_404(recruiter_client: APIClient) -> None:
    assert recruiter_client.get(POOL).status_code == 404


def test_anonymous_is_refused(api_client: APIClient) -> None:
    assert api_client.get(POOL).status_code in (401, 403)
