from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.models import Stage
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.companies.models import Company
from apps.jobs.models import Job, JobStatus
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db

HOME = "/api/v1/company/home"


def _application(job: Job, recruiter: RecruiterProfile, stage: str) -> None:
    ApplicationFactory(
        job=job, candidate=CandidateFactory(owner=recruiter), recruiter=recruiter, stage=stage
    )


def test_home_counts_jobs_and_pipeline(
    client_for: Callable[[User], APIClient],
    company: Company,
    company_ta: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    active = JobFactory(company=company, title="Senior Backend Engineer")
    JobFactory(company=company, title="Data Analyst", status=JobStatus.PENDING)
    JobFactory(company=company, title="QA Engineer", status=JobStatus.CLOSED)
    _application(active, recruiter_profile, Stage.NEW)
    _application(active, recruiter_profile, Stage.INTERVIEW)

    body = client_for(company_ta).get(HOME).json()

    assert body["jobs"] == {"pending": 1, "active": 1, "closed": 1}
    assert body["pipeline"]["new"] == 1
    assert body["pipeline"]["interview"] == 1
    assert body["pipeline"]["offer"] == 0


def test_home_lists_active_jobs_with_their_metrics(
    client_for: Callable[[User], APIClient],
    company: Company,
    company_ta: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    job = JobFactory(company=company, title="Senior Backend Engineer")
    _application(job, recruiter_profile, Stage.NEW)
    JobFactory(company=company, title="Closed Role", status=JobStatus.CLOSED)

    rows = client_for(company_ta).get(HOME).json()["active_jobs"]

    assert [row["title"] for row in rows] == ["Senior Backend Engineer"]
    assert rows[0]["cv_count"] == 1
    assert rows[0]["new_count"] == 1
    assert rows[0]["current_stage"] == "new"
    assert rows[0]["days_open"] >= 0


def test_the_action_center_asks_for_the_decisions_that_are_waiting(
    client_for: Callable[[User], APIClient],
    company: Company,
    company_ta: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    waiting = JobFactory(company=company, title="Data Analyst", status=JobStatus.PENDING)
    busy = JobFactory(company=company, title="Senior Backend Engineer")
    _application(busy, recruiter_profile, Stage.NEW)
    _application(busy, recruiter_profile, Stage.NEW)

    actions = client_for(company_ta).get(HOME).json()["actions"]

    assert actions[0] == {
        "kind": "job_pending",
        "title": "Data Analyst",
        "detail": "Waiting for Sparktal to approve",
        "href": f"/jobs/{waiting.pk}",
    }
    assert actions[1] == {
        "kind": "new_applicants",
        "title": "Senior Backend Engineer",
        "detail": "2 new CVs to review",
        "href": f"/jobs/{busy.pk}/applicants",
    }


def test_a_quiet_company_gets_no_actions(
    client_for: Callable[[User], APIClient], company: Company, company_ta: User
) -> None:
    JobFactory(company=company, title="Senior Backend Engineer")

    body = client_for(company_ta).get(HOME).json()

    assert body["actions"] == []


def test_a_hiring_manager_only_sees_the_jobs_granted_to_them(
    client_for: Callable[[User], APIClient], company: Company, company_hm: User
) -> None:
    JobFactory(company=company, title="Senior Backend Engineer")

    body = client_for(company_hm).get(HOME).json()

    assert body["jobs"] == {"pending": 0, "active": 0, "closed": 0}
    assert body["active_jobs"] == []


def test_another_company_never_leaks_in(
    client_for: Callable[[User], APIClient], company: Company, other_company_admin: User
) -> None:
    JobFactory(company=company, title="Senior Backend Engineer")

    body = client_for(other_company_admin).get(HOME).json()

    assert body["active_jobs"] == []


def test_a_recruiter_has_no_company_home(
    client_for: Callable[[User], APIClient], recruiter_user: User
) -> None:
    assert client_for(recruiter_user).get(HOME).status_code == 404


def test_signing_out_closes_the_home(api_client: APIClient) -> None:
    assert api_client.get(HOME).status_code == 403
