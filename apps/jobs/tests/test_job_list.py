from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.models import Stage
from apps.candidates.tests.factories import ApplicationFactory
from apps.companies.models import Company
from apps.jobs.access import grant_creator_perms
from apps.jobs.models import JobStatus
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db

URL = "/api/v1/jobs"

ClientFor = Callable[[User], APIClient]


@pytest.fixture
def jobs(company: Company) -> dict[str, Any]:
    now = timezone.now()
    backend = JobFactory(
        company=company,
        title="Senior Backend Engineer",
        function="engineering",
        status=JobStatus.ACTIVE,
        published_at=now - timedelta(days=38),
    )
    analyst = JobFactory(
        company=company,
        title="Data Analyst",
        function="data",
        status=JobStatus.ACTIVE,
        published_at=now - timedelta(days=25),
    )
    JobFactory(
        company=company,
        title="Head of Finance",
        function="finance",
        status=JobStatus.PENDING,
        published_at=None,
    )
    JobFactory(company=company, title="QA Engineer", function="other", status=JobStatus.CLOSED)

    ApplicationFactory(job=backend, stage=Stage.NEW)
    ApplicationFactory(job=backend, stage=Stage.NEW)
    ApplicationFactory(job=backend, stage=Stage.INTERVIEW)
    ApplicationFactory(job=backend, stage=Stage.REJECTED)
    ApplicationFactory(job=analyst, stage=Stage.SCREEN)
    return {"backend": backend, "analyst": analyst}


def test_list_defaults_to_the_active_tab_shape(
    client_for: ClientFor, company_ta: User, jobs: dict[str, Any]
) -> None:
    response = client_for(company_ta).get(URL, {"status": "active"})

    assert response.status_code == 200
    titles = [row["title"] for row in response.json()["results"]]
    assert set(titles) == {"Senior Backend Engineer", "Data Analyst"}


def test_list_row_carries_the_derived_numbers(
    client_for: ClientFor, company_ta: User, jobs: dict[str, Any]
) -> None:
    response = client_for(company_ta).get(URL, {"search": "Senior Backend"})

    row = response.json()["results"][0]
    assert row["cv_count"] == 4
    assert row["new_count"] == 2
    assert row["current_stage"] == "interview"
    assert row["days_open"] == 38
    assert row["days_to_onboard"] is None
    assert row["function"] == "engineering"


def test_days_to_onboard_counts_to_the_first_onboarded_application(
    client_for: ClientFor, company_ta: User, company: Company
) -> None:
    now = timezone.now()
    job = JobFactory(company=company, title="Ops Lead", published_at=now - timedelta(days=50))
    ApplicationFactory(job=job, stage=Stage.ONBOARDED, stage_changed_at=now - timedelta(days=20))

    row = client_for(company_ta).get(URL, {"search": "Ops Lead"}).json()["results"][0]

    assert row["days_to_onboard"] == 30


def test_search_and_function_filters(
    client_for: ClientFor, company_ta: User, jobs: dict[str, Any]
) -> None:
    client = client_for(company_ta)

    assert client.get(URL, {"search": "analyst"}).json()["count"] == 1
    assert client.get(URL, {"function": "engineering"}).json()["count"] == 1
    assert client.get(URL, {"search": "nothing here"}).json()["count"] == 0


@pytest.mark.parametrize(
    ("ordering", "first"),
    [
        ("title", "Data Analyst"),
        ("-title", "Senior Backend Engineer"),
        ("-cv_count", "Senior Backend Engineer"),
        ("days_open", "Data Analyst"),
    ],
)
def test_ordering(
    client_for: ClientFor, company_ta: User, jobs: dict[str, Any], ordering: str, first: str
) -> None:
    response = client_for(company_ta).get(URL, {"status": "active", "ordering": ordering})

    assert response.json()["results"][0]["title"] == first


def test_counts_endpoint(client_for: ClientFor, company_ta: User, jobs: dict[str, Any]) -> None:
    response = client_for(company_ta).get(f"{URL}/counts")

    assert response.json() == {"pending": 1, "active": 2, "closed": 1}


def test_hiring_manager_sees_only_granted_jobs(
    client_for: ClientFor, company_hm: User, jobs: dict[str, Any]
) -> None:
    grant_creator_perms(jobs["analyst"], company_hm)

    client = client_for(company_hm)

    assert [row["title"] for row in client.get(URL).json()["results"]] == ["Data Analyst"]
    assert client.get(f"{URL}/counts").json() == {"pending": 0, "active": 1, "closed": 0}


def test_list_query_count_is_flat(
    client_for: ClientFor,
    company_ta: User,
    jobs: dict[str, Any],
    django_assert_max_num_queries: Any,
) -> None:
    client = client_for(company_ta)  # logging in costs queries of its own

    with django_assert_max_num_queries(6):
        client.get(URL, {"status": "active"})
