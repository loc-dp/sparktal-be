import pytest
from django.test import Client

from apps.accounts.models import User
from apps.companies.models import Company
from apps.jobs.models import JobStatus
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(db: None) -> Client:
    User.objects.create_superuser(
        email="admin@sparktal.vn", password="pass-1234-word", full_name="Sparktal Admin"
    )
    client = Client()
    client.login(email="admin@sparktal.vn", password="pass-1234-word")
    return client


def test_approve_action_publishes_pending_jobs(staff_client: Client, company: Company) -> None:
    job = JobFactory(company=company, status=JobStatus.PENDING, published_at=None)

    response = staff_client.post(
        "/admin/jobs/job/",
        {"action": "approve_jobs", "_selected_action": [str(job.pk)]},
        follow=True,
    )

    assert response.status_code == 200
    job.refresh_from_db()
    assert job.status == JobStatus.ACTIVE
    assert job.published_at is not None


def test_approve_action_leaves_closed_jobs_alone(staff_client: Client, company: Company) -> None:
    job = JobFactory(company=company, status=JobStatus.CLOSED)

    staff_client.post(
        "/admin/jobs/job/",
        {"action": "approve_jobs", "_selected_action": [str(job.pk)]},
        follow=True,
    )

    job.refresh_from_db()
    assert job.status == JobStatus.CLOSED
