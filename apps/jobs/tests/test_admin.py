from typing import Any, cast

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company
from apps.jobs.models import Job, JobAssignment, JobStatus
from apps.jobs.tests.admin_helpers import job_change_form
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(db: None) -> Client:
    User.objects.create_superuser(
        email="admin@sparktal.vn", password="pass-1234-word", full_name="Sparktal Admin"
    )
    client = Client()
    client.login(email="admin@sparktal.vn", password="pass-1234-word")
    return client


def test_restore_action_brings_back_deleted_jobs(staff_client: Client, company: Company) -> None:
    job = JobFactory(company=company, deleted_at=timezone.now())

    staff_client.post(
        "/admin/jobs/job/",
        {"action": "restore_jobs", "_selected_action": [str(job.pk)]},
        follow=True,
    )

    job.refresh_from_db()
    assert job.deleted_at is None


def test_admin_status_is_read_only(staff_client: Client, company: Company) -> None:
    job = JobFactory(company=company, status=JobStatus.PENDING)

    response = staff_client.get(f"/admin/jobs/job/{job.pk}/change/")

    assert response.status_code == 200
    form = response.context["adminform"].form
    for name in ("status", "posted_at", "published_at", "deleted_at"):
        assert name not in form.fields
    for name in ("bounty_type", "bounty_pct", "bounty_amount", "guarantee_days"):
        assert name in form.fields


# `on_marketplace` joins them only while the marketplace is on (see the tests at the bottom).
ADMIN_EDITABLE = {
    "bounty_type",
    "bounty_pct",
    "bounty_amount",
    "guarantee_days",
}


def test_admin_edits_only_the_bounty_the_guarantee_and_the_channel_of_an_existing_job(
    staff_client: Client, company: Company, company_ta: User
) -> None:
    # Spec decision 15: Django Admin keeps the bounty as a fallback; ops sees the JD read-only.
    job = JobFactory(company=company, created_by=company_ta, title="Platform Engineer")

    response = staff_client.get(f"/admin/jobs/job/{job.pk}/change/")

    assert response.status_code == 200
    assert set(response.context["adminform"].form.fields) == ADMIN_EDITABLE
    assert "Platform Engineer" in response.content.decode()  # still readable


def test_admin_add_view_still_takes_the_job_content(staff_client: Client) -> None:
    response = staff_client.get("/admin/jobs/job/add/")

    assert response.status_code == 200
    fields = set(response.context["adminform"].form.fields)
    assert {"company", "title", "function", "why_we_need_you"} <= fields
    assert fields >= ADMIN_EDITABLE


def test_a_stale_admin_form_does_not_revert_what_the_company_changed(
    staff_client: Client, company: Company, company_ta: User
) -> None:
    job = JobFactory(
        company=company,
        created_by=company_ta,
        title="Platform Engineer",
        why_we_need_you="The original brief.",
        status=JobStatus.PENDING,
        on_marketplace=False,
        published_at=None,
        bounty_type=None,
        bounty_pct=None,
    )
    url, data = job_change_form(staff_client, job, bounty_type="pct", bounty_pct="20")
    # Staff open the form; meanwhile the company edits the job.
    Job.objects.filter(pk=job.pk).update(
        title="Staff Platform Engineer", why_we_need_you="New brief."
    )

    # A browser holding the old page may still post the old content. It is ignored.
    data.update({"title": "Platform Engineer", "why_we_need_you": "The original brief."})
    assert staff_client.post(url, data).status_code == 302

    job.refresh_from_db()
    assert (job.title, job.why_we_need_you) == ("Staff Platform Engineer", "New brief.")
    assert (job.bounty_type, job.bounty_pct, job.bounty_amount) == ("pct", 20, None)


def test_admin_edits_the_guarantee_days_and_rejects_out_of_range(
    staff_client: Client, company: Company, company_ta: User
) -> None:
    job = JobFactory(company=company, created_by=company_ta)
    url, data = job_change_form(staff_client, job, guarantee_days="90")

    assert staff_client.post(url, data).status_code == 302

    job.refresh_from_db()
    assert job.guarantee_days == 90
    for bad in ("0", "366"):
        url, data = job_change_form(staff_client, job, guarantee_days=bad)
        response = staff_client.post(url, data)
        assert response.status_code == 200  # the form comes back with its error
        assert "guarantee_days" in response.context["adminform"].form.errors
    job.refresh_from_db()
    assert job.guarantee_days == 90


def test_a_posted_title_never_changes_the_job(
    staff_client: Client, company: Company, company_ta: User
) -> None:
    job = JobFactory(company=company, created_by=company_ta, title="Platform Engineer")
    url, data = job_change_form(staff_client, job, title="Renamed in the admin", bounty_pct="22")

    assert staff_client.post(url, data).status_code == 302

    job.refresh_from_db()
    assert (job.title, job.bounty_pct) == ("Platform Engineer", 22)


@pytest.mark.parametrize(
    ("overrides", "field", "message"),
    [
        ({"bounty_type": "pct", "bounty_pct": "99"}, "bounty_pct", "between 5 and 30"),
        ({"bounty_type": "fixed", "bounty_amount": ""}, "bounty_amount", "fixed bounty amount"),
        ({"bounty_type": ""}, "bounty_type", "Pick a bounty type"),
    ],
)
def test_admin_still_refuses_a_bad_bounty_on_a_queued_job_with_the_content_read_only(
    staff_client: Client,
    company: Company,
    company_ta: User,
    overrides: dict[str, str],
    field: str,
    message: str,
) -> None:
    job = JobFactory(
        company=company,
        created_by=company_ta,
        title="Platform Engineer",
        status=JobStatus.PENDING,
        on_marketplace=False,
        published_at=None,
        bounty_type=None,
        bounty_pct=None,
    )
    url, data = job_change_form(staff_client, job, title="Renamed in the admin", **overrides)

    response = staff_client.post(url, data)

    assert response.status_code == 200
    assert message in str(response.context["adminform"].form.errors[field])
    job.refresh_from_db()
    assert (job.title, job.bounty_type, job.bounty_pct) == ("Platform Engineer", None, None)


def test_view_only_staff_do_not_get_the_job_actions(company: Company) -> None:
    from django.contrib.admin.sites import site
    from django.contrib.auth.models import Permission
    from django.test import RequestFactory

    viewer = User.objects.create_user(
        email="view@sparktal.vn", password="pass-1234-word", full_name="Viewer", is_staff=True
    )
    viewer.user_permissions.add(Permission.objects.get(codename="view_job"))
    request = RequestFactory().get("/admin/jobs/job/")
    request.user = User.objects.get(pk=viewer.pk)

    assert "restore_jobs" not in site._registry[Job].get_actions(request)


def test_admin_form_does_not_ask_a_private_job_for_a_bounty(
    staff_client: Client, company: Company, company_ta: User
) -> None:
    draft = JobFactory(
        company=company,
        created_by=company_ta,
        status=JobStatus.DRAFT,
        bounty_type=None,
        bounty_pct=None,
        published_at=None,
    )
    url, data = job_change_form(staff_client, draft)

    response = staff_client.post(url, data)

    assert response.status_code == 302
    draft.refresh_from_db()
    assert draft.bounty_type is None


def _private_job(company: Company, creator: User, status: str) -> Job:
    return cast(
        Job,
        JobFactory(
            company=company,
            created_by=creator,
            status=status,
            bounty_type=None,
            bounty_pct=None,
            published_at=None,
            on_marketplace=False,
        ),
    )


@pytest.mark.parametrize("status", [JobStatus.DRAFT, JobStatus.SUBMITTED])
@pytest.mark.parametrize(
    ("overrides", "field", "message"),
    [
        ({"bounty_type": "pct", "bounty_pct": "99"}, "bounty_pct", "between 5 and 30"),
        ({"bounty_type": "pct", "bounty_pct": "4"}, "bounty_pct", "between 5 and 30"),
        ({"bounty_type": "", "bounty_pct": "18"}, "bounty_type", "Pick a bounty type"),
        ({"bounty_type": "fixed", "bounty_pct": "18"}, "bounty_amount", "fixed bounty amount"),
        (
            {"bounty_type": "pct", "bounty_pct": "99", "bounty_amount": "90000000"},
            "bounty_pct",
            "between 5 and 30",
        ),
    ],
)
def test_admin_form_checks_a_bounty_entered_on_a_private_job(
    staff_client: Client,
    company: Company,
    company_ta: User,
    status: str,
    overrides: dict[str, str],
    field: str,
    message: str,
) -> None:
    job = _private_job(company, company_ta, status)
    url, data = job_change_form(staff_client, job, **overrides)

    response = staff_client.post(url, data)

    assert response.status_code == 200
    assert message in str(response.context["adminform"].form.errors[field])
    job.refresh_from_db()
    assert (job.bounty_type, job.bounty_pct, job.bounty_amount) == (None, None, None)


@pytest.mark.parametrize("status", [JobStatus.DRAFT, JobStatus.SUBMITTED])
@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"bounty_type": "pct", "bounty_pct": "18"}, ("pct", 18, None)),
        (
            {"bounty_type": "pct", "bounty_pct": "18", "bounty_amount": "90000000"},
            ("pct", 18, None),
        ),
        (
            {"bounty_type": "fixed", "bounty_pct": "99", "bounty_amount": "90000000"},
            ("fixed", None, 90_000_000),
        ),
    ],
)
def test_admin_form_saves_a_valid_bounty_on_a_private_job_normalised(
    staff_client: Client,
    company: Company,
    company_ta: User,
    status: str,
    overrides: dict[str, str],
    expected: tuple[str, int | None, int | None],
) -> None:
    job = _private_job(company, company_ta, status)
    url, data = job_change_form(staff_client, job, **overrides)

    assert staff_client.post(url, data).status_code == 302

    job.refresh_from_db()
    assert (job.bounty_type, job.bounty_pct, job.bounty_amount) == expected


def test_admin_sets_the_bounty_of_a_pending_job(
    staff_client: Client, company: Company, company_ta: User
) -> None:
    job = JobFactory(
        company=company,
        created_by=company_ta,
        status=JobStatus.PENDING,
        published_at=None,
        on_marketplace=False,
        bounty_type=None,
        bounty_pct=None,
    )
    url, data = job_change_form(staff_client, job)
    refused = staff_client.post(url, data)  # a queued job is not saved without its bounty
    assert refused.status_code == 200
    assert "Pick a bounty type" in str(refused.context["adminform"].form.errors["bounty_type"])

    url, data = job_change_form(staff_client, job, bounty_type="pct", bounty_pct="20")
    assert staff_client.post(url, data).status_code == 302

    job.refresh_from_db()
    # Setting the bounty does not distribute: that is the ops portal's, with recruiters picked.
    assert (job.status, job.bounty_pct) == (JobStatus.PENDING, 20)


def test_admin_on_marketplace_is_read_only_while_the_marketplace_is_off(
    staff_client: Client, company: Company, company_ta: User, settings: Any
) -> None:
    settings.MARKETPLACE_ENABLED = False
    job = JobFactory(company=company, created_by=company_ta, on_marketplace=True)

    for url in (f"/admin/jobs/job/{job.pk}/change/", "/admin/jobs/job/add/"):
        response = staff_client.get(url)
        assert response.status_code == 200
        assert "on_marketplace" not in response.context["adminform"].form.fields
    url, data = job_change_form(staff_client, job)
    data["on_marketplace"] = ""  # a forged post cannot clear it either

    assert staff_client.post(url, data).status_code == 302

    job.refresh_from_db()
    assert job.on_marketplace is True


def test_admin_offers_on_marketplace_once_the_marketplace_is_on(
    staff_client: Client, company: Company, company_ta: User, settings: Any
) -> None:
    settings.MARKETPLACE_ENABLED = True
    job = JobFactory(company=company, created_by=company_ta)

    for url in (f"/admin/jobs/job/{job.pk}/change/", "/admin/jobs/job/add/"):
        response = staff_client.get(url)
        assert "on_marketplace" in response.context["adminform"].form.fields


def test_admin_form_edits_on_marketplace(
    staff_client: Client, company: Company, company_ta: User, settings: Any
) -> None:
    settings.MARKETPLACE_ENABLED = True
    job = JobFactory(company=company, created_by=company_ta, on_marketplace=True)
    url, data = job_change_form(staff_client, job)
    data.pop("on_marketplace")  # an unticked box is not posted

    assert staff_client.post(url, data).status_code == 302

    job.refresh_from_db()
    assert job.on_marketplace is False


def test_jobs_have_no_distribute_action_and_invites_are_gone(staff_client: Client) -> None:
    from django.contrib.admin.sites import site
    from django.test import RequestFactory

    request = RequestFactory().get("/admin/jobs/job/")
    request.user = User.objects.get(email="admin@sparktal.vn")
    model_admin = site._registry[Job]

    # Distributing needs recruiters picked, so it happens only in the ops portal.
    assert "distribute_to_marketplace" not in model_admin.get_actions(request)
    assert model_admin.inlines == ()
    assert staff_client.get("/admin/jobs/jobinvite/").status_code == 404


def test_assignments_stay_editable_in_the_admin(staff_client: Client, company: Company) -> None:
    """The fallback when the ops portal can't: put a recruiter on a job, or take them off."""
    job = JobFactory(company=company, title="Growth Analyst")
    recruiter = RecruiterProfileFactory(nickname="Bluefin Search")

    added = staff_client.post(
        "/admin/jobs/jobassignment/add/", {"job": str(job.pk), "recruiter": str(recruiter.pk)}
    )

    assert added.status_code == 302
    assignment = JobAssignment.objects.get(job=job, recruiter=recruiter)
    listing = staff_client.get("/admin/jobs/jobassignment/")
    assert listing.status_code == 200
    assert "Bluefin Search" in listing.content.decode()
    changed = staff_client.post(
        f"/admin/jobs/jobassignment/{assignment.pk}/change/",
        {
            "job": str(job.pk),
            "recruiter": str(recruiter.pk),
            "left_at_0": "2026-11-20",
            "left_at_1": "10:00:00",
        },
    )
    assert changed.status_code == 302
    assignment.refresh_from_db()
    assert assignment.left_at is not None
