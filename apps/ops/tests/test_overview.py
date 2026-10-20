from datetime import timedelta
from typing import Any, cast

from django.utils import timezone
from rest_framework.test import APIClient

from apps.candidates.models import Stage
from apps.candidates.tests.factories import ApplicationFactory
from apps.companies.models import Company, Team
from apps.companies.tests.factories import CompanyFactory, OfficeFactory, TeamFactory
from apps.jobs.models import (
    InviteStatus,
    Job,
    JobAssignment,
    JobStatus,
)
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory, JobInviteFactory
from apps.ops.tests.helpers import ITEM_KEYS, queued_job
from apps.recruiters.models import RecruiterStatus
from apps.recruiters.tests.factories import RecruiterProfileFactory

OVERVIEW = "/api/v1/ops/overview"
QUEUE_LIMIT = 5


def test_overview_counts(staff_client: APIClient) -> None:
    company = cast(Company, CompanyFactory())
    OfficeFactory(company=company)
    queued_job(company, posted_ago_days=1)
    active_jobs = [JobFactory(company=company, status=JobStatus.ACTIVE) for _ in range(2)]
    JobFactory(company=company, status=JobStatus.ACTIVE, deleted_at=timezone.now())
    # Not counted anywhere: still with the company, or finished.
    JobFactory(company=company, status=JobStatus.DRAFT)
    JobFactory(company=company, status=JobStatus.SUBMITTED)
    JobFactory(company=company, status=JobStatus.CLOSED)
    queued_job(company, posted_ago_days=2, deleted_at=timezone.now())
    # Three CVs on a live job, one of them hired.
    for stage in (Stage.NEW, Stage.INTERVIEW, Stage.ONBOARDED):
        ApplicationFactory(job=active_jobs[0], stage=stage)
    # Recruiters: ApplicationFactory made three approved ones; add the other statuses.
    RecruiterProfileFactory.create_batch(2, status=RecruiterStatus.PENDING)
    RecruiterProfileFactory(status=RecruiterStatus.REJECTED)
    CompanyFactory()

    body = staff_client.get(OVERVIEW).json()

    assert body["companies"] == 2
    assert body["active_jobs"] == 2
    assert body["queued_jobs"] == 1
    assert body["pending_recruiters"] == 2
    assert body["cvs_submitted"] == 3
    assert body["hires"] == 1
    assert [item["id"] for item in body["queue"]] == [
        str(Job.objects.get(status=JobStatus.PENDING, deleted_at__isnull=True).pk)
    ]


def test_overview_cvs_and_hires_count_closed_jobs_too(staff_client: APIClient) -> None:
    """A hire stays a hire after its job closes: the totals are platform history, not live jobs."""
    company = cast(Company, CompanyFactory())
    closed = JobFactory(company=company, status=JobStatus.CLOSED)
    ApplicationFactory(job=closed, stage=Stage.ONBOARDED)
    ApplicationFactory(job=closed, stage=Stage.NEW)
    live = JobFactory(company=company, status=JobStatus.ACTIVE)
    ApplicationFactory(job=live, stage=Stage.ONBOARDED)

    body = staff_client.get(OVERVIEW).json()

    assert body["cvs_submitted"] == 3
    assert body["hires"] == 2


def test_overview_of_an_empty_platform(staff_client: APIClient) -> None:
    assert staff_client.get(OVERVIEW).json() == {
        "companies": 0,
        "active_jobs": 0,
        "queued_jobs": 0,
        "pending_recruiters": 0,
        "cvs_submitted": 0,
        "hires": 0,
        "queue": [],
    }


def test_overview_queue_is_oldest_first(staff_client: APIClient) -> None:
    company = cast(Company, CompanyFactory())
    ages = [3, 9, 1, 7, 5, 8, 2]
    jobs = {age: queued_job(company, posted_ago_days=age) for age in ages}
    # Older than all of them, but not waiting for Sparktal.
    queued_job(company, posted_ago_days=30, status=JobStatus.ACTIVE)
    queued_job(company, posted_ago_days=20, deleted_at=timezone.now())

    body = staff_client.get(OVERVIEW).json()

    assert body["queued_jobs"] == len(ages)
    assert [item["id"] for item in body["queue"]] == [
        str(jobs[age].pk) for age in sorted(ages, reverse=True)[:QUEUE_LIMIT]
    ]


def test_overview_queue_breaks_ties_by_id(staff_client: APIClient) -> None:
    company = cast(Company, CompanyFactory())
    posted_at = timezone.now() - timedelta(days=2)
    jobs = [
        queued_job(company, posted_ago_days=0, posted_at=posted_at) for _ in range(QUEUE_LIMIT + 1)
    ]

    body = staff_client.get(OVERVIEW).json()

    assert [item["id"] for item in body["queue"]] == [
        str(pk) for pk in sorted(job.pk for job in jobs)[:QUEUE_LIMIT]
    ]


def test_overview_queue_item_has_the_ops_job_list_shape(staff_client: APIClient) -> None:
    company = cast(Company, CompanyFactory(name="Vela Pay"))
    team = cast(Team, TeamFactory(company=company, name="Payments"))
    job = queued_job(company, posted_ago_days=1, team=team, title="Payments Lead")
    for status in (
        InviteStatus.PENDING,
        InviteStatus.PENDING,
        InviteStatus.ACCEPTED,
        InviteStatus.DECLINED,
        InviteStatus.CANCELLED,
    ):
        JobInviteFactory(job=job, status=status)
    JobAssignmentFactory.create_batch(2, job=job)
    JobAssignmentFactory(job=job, left_at=timezone.now())
    # Another job's invites and assignments never leak into the counts.
    JobInviteFactory(job=queued_job(company, posted_ago_days=0), status=InviteStatus.PENDING)

    items = {item["id"]: item for item in staff_client.get(OVERVIEW).json()["queue"]}
    item = items[str(job.pk)]

    assert set(item) == ITEM_KEYS
    assert item["title"] == "Payments Lead"
    assert item["company"] == {"id": str(company.pk), "name": "Vela Pay"}
    assert item["team_name"] == "Payments"
    assert item["status"] == "pending"
    assert item["posted_at"] is not None
    assert item["published_at"] is None
    assert item["bounty_type"] is None
    assert item["bounty_pct"] is None
    assert item["bounty_amount"] is None
    assert item["on_marketplace"] is False
    assert item["invites"] == {"pending": 2, "accepted": 1, "declined": 1}
    assert item["recruiters_working"] == 2
    assert JobAssignment.objects.filter(job=job).count() == 3


def test_overview_queue_item_without_a_team(staff_client: APIClient) -> None:
    company = cast(Company, CompanyFactory())
    job = queued_job(company, posted_ago_days=1, team=None)

    (item,) = staff_client.get(OVERVIEW).json()["queue"]

    assert item["id"] == str(job.pk)
    assert item["team_name"] is None
    assert item["invites"] == {"pending": 0, "accepted": 0, "declined": 0}
    assert item["recruiters_working"] == 0


def test_overview_queries_do_not_grow_with_the_queue(
    staff_client: APIClient, django_assert_max_num_queries: Any
) -> None:
    company = cast(Company, CompanyFactory())
    for age in range(QUEUE_LIMIT):
        job = queued_job(company, posted_ago_days=age + 1, team=TeamFactory(company=company))
        JobInviteFactory(job=job)
    # Session and user, then five reads for the page; none of them per queued job.
    with django_assert_max_num_queries(7):
        assert len(staff_client.get(OVERVIEW).json()["queue"]) == QUEUE_LIMIT
