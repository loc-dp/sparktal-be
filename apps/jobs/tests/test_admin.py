import pytest
from django.test import Client
from django.utils import timezone

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


def test_restore_action_brings_back_deleted_jobs(staff_client: Client, company: Company) -> None:
    job = JobFactory(company=company, deleted_at=timezone.now())

    staff_client.post(
        "/admin/jobs/job/",
        {"action": "restore_jobs", "_selected_action": [str(job.pk)]},
        follow=True,
    )

    job.refresh_from_db()
    assert job.deleted_at is None


def test_status_and_deleted_at_are_read_only_in_the_change_form(
    staff_client: Client, company: Company
) -> None:
    job = JobFactory(company=company, status=JobStatus.PENDING)

    response = staff_client.get(f"/admin/jobs/job/{job.pk}/change/")

    assert response.status_code == 200
    form = response.context["adminform"].form
    assert "status" not in form.fields
    assert "deleted_at" not in form.fields


def test_view_only_staff_do_not_get_the_job_actions(company: Company) -> None:
    from django.contrib.admin.sites import site
    from django.contrib.auth.models import Permission
    from django.test import RequestFactory

    from apps.jobs.models import Job

    viewer = User.objects.create_user(
        email="view@sparktal.vn", password="pass-1234-word", full_name="Viewer", is_staff=True
    )
    viewer.user_permissions.add(Permission.objects.get(codename="view_job"))
    request = RequestFactory().get("/admin/jobs/job/")
    request.user = User.objects.get(pk=viewer.pk)

    assert "approve_jobs" not in site._registry[Job].get_actions(request)
    assert "restore_jobs" not in site._registry[Job].get_actions(request)


def test_approve_skips_a_job_that_is_no_longer_pending(
    staff_client: Client, company: Company
) -> None:
    pending = JobFactory(company=company, status=JobStatus.PENDING, published_at=None)
    closed = JobFactory(company=company, status=JobStatus.CLOSED)

    response = staff_client.post(
        "/admin/jobs/job/",
        {"action": "approve_jobs", "_selected_action": [str(closed.pk), str(pending.pk)]},
        follow=True,
    )

    assert response.status_code == 200
    pending.refresh_from_db()
    assert pending.status == JobStatus.ACTIVE
