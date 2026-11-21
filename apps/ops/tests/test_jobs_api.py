"""Sparktal ops: the job queue, one job, its bounty, its distribution, its headhunters and the
marketplace switch."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.candidates.tests.factories import ApplicationFactory
from apps.companies.models import Company, Team
from apps.companies.tests.factories import CompanyFactory, OfficeFactory, TeamFactory
from apps.jobs.models import Job, JobAssignment, JobStatus
from apps.jobs.serializers import MarketplaceJobSerializer
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory
from apps.ops.tests.helpers import ITEM_KEYS, queued_job
from apps.recruiters.models import RecruiterProfile, RecruiterStatus
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

JOBS = "/api/v1/ops/jobs"
NOT_WAITING = "This job is no longer waiting for distribution."
NEEDS_RECRUITER = "Pick at least one recruiter."
LAST_RECRUITER = "A live job needs at least one recruiter. Assign someone else first."
NOT_DISTRIBUTED = "This job hasn't been distributed yet."
NOT_LIVE = "Only a live job can take recruiters."
PCT_RANGE = "Bounty must be between 5 and 30%"
AMOUNT_MISSING = "Enter the fixed bounty amount"
AMOUNT_SIZE = "Enter a whole amount of up to 15 digits."
PICK_TYPE = "Pick a bounty type."
UNKNOWN_RECRUITER = "Unknown recruiter."

DETAIL_KEYS = {
    "id",
    "title",
    "function",
    "company",
    "team_name",
    "status",
    "posted_at",
    "published_at",
    "on_marketplace",
    "bounty_type",
    "bounty_pct",
    "bounty_amount",
    "guarantee_days",
    "why_we_need_you",
    "roles",
    "qualifications",
    "nice_to_have",
    "benefits",
    "skills",
    "work_model",
    "location_label",
    "salary_min",
    "salary_max",
    "pay_basis",
    "working_hours",
    "reporting_line",
    "role_type",
    "has_internal_candidate",
    "search_duration",
    "target_start_date",
    "interview_rounds_count",
    "interview_process_details",
    "languages",
    "core_competencies",
    "probation_expectations",
    "salary_negotiable",
    "allowances",
    "note_for_recruiters",
    "recruiters_working",
    "assignments",
}
# On the marketplace detail only because it is a recruiter looking at the job.
RECRUITER_ONLY = {"joined", "submitted_count", "channel_id", "created_at"}

Commit = Callable[..., Any]


def detail_url(job: Job) -> str:
    return f"{JOBS}/{job.pk}"


def distribute(client: APIClient, job: Job, **payload: Any) -> Any:
    """Distribute with a 20% bounty and, unless `recruiter_ids` is given, one new recruiter."""
    body = {
        "bounty_type": "pct",
        "bounty_pct": 20,
        "recruiter_ids": ids([make_recruiter()]),
        **payload,
    }
    return client.post(f"{detail_url(job)}/distribute", body, format="json")


def put_bounty(client: APIClient, job: Job, payload: dict[str, Any]) -> Any:
    return client.put(f"{detail_url(job)}/bounty", payload, format="json")


def assign(client: APIClient, job: Job, recruiter_ids: list[str]) -> Any:
    return client.post(
        f"{detail_url(job)}/assignments", {"recruiter_ids": recruiter_ids}, format="json"
    )


def remove(client: APIClient, job: Job, recruiter: RecruiterProfile) -> Any:
    return client.delete(f"{detail_url(job)}/assignments/{recruiter.pk}")


def patch_channels(client: APIClient, job: Job, payload: dict[str, Any]) -> Any:
    return client.patch(f"{detail_url(job)}/channels", payload, format="json")


def make_recruiter(**fields: Any) -> RecruiterProfile:
    return cast(RecruiterProfile, RecruiterProfileFactory(**fields))


def ids(recruiters: list[RecruiterProfile]) -> list[str]:
    return [str(recruiter.pk) for recruiter in recruiters]


def live_job(**fields: Any) -> Job:
    """A job Sparktal already distributed: active, 18%, on the marketplace."""
    return cast(Job, JobFactory(**{"status": JobStatus.ACTIVE, **fields}))


@pytest.fixture
def marketplace_on(settings: Any) -> None:
    settings.MARKETPLACE_ENABLED = True


def held(job: Job) -> set[Any]:
    """The recruiters on `job` now."""
    return set(
        JobAssignment.objects.filter(job=job, left_at__isnull=True).values_list(
            "recruiter_id", flat=True
        )
    )


# Every case an ops bounty form can get wrong: the field the 400 is keyed by, and the exact
# sentence it carries. They are `clean_bounty`'s own, so the FE forms say the same ones.
BAD_BOUNTIES: list[tuple[dict[str, Any], str, str]] = [
    ({"bounty_type": "pct", "bounty_pct": 4}, "bounty_pct", PCT_RANGE),
    ({"bounty_type": "pct", "bounty_pct": 31}, "bounty_pct", PCT_RANGE),
    ({"bounty_type": "pct", "bounty_pct": 0}, "bounty_pct", PCT_RANGE),
    ({"bounty_type": "pct", "bounty_pct": -10}, "bounty_pct", PCT_RANGE),
    ({"bounty_type": "pct", "bounty_pct": 10**30}, "bounty_pct", PCT_RANGE),
    # Not a whole number, or not a number at all: the same range sentence, not DRF's.
    ({"bounty_type": "pct", "bounty_pct": 18.5}, "bounty_pct", PCT_RANGE),
    ({"bounty_type": "pct", "bounty_pct": "18.5"}, "bounty_pct", PCT_RANGE),
    ({"bounty_type": "pct", "bounty_pct": "abc"}, "bounty_pct", PCT_RANGE),
    ({"bounty_type": "pct", "bounty_pct": ""}, "bounty_pct", PCT_RANGE),
    ({"bounty_type": "pct", "bounty_pct": "9" * 2000}, "bounty_pct", PCT_RANGE),
    ({"bounty_type": "pct"}, "bounty_pct", PCT_RANGE),
    ({"bounty_type": "pct", "bounty_pct": None}, "bounty_pct", PCT_RANGE),
    ({"bounty_type": "fixed", "bounty_amount": 0}, "bounty_amount", AMOUNT_MISSING),
    ({"bounty_type": "fixed", "bounty_amount": -5}, "bounty_amount", AMOUNT_MISSING),
    ({"bounty_type": "fixed"}, "bounty_amount", AMOUNT_MISSING),
    ({"bounty_type": "fixed", "bounty_amount": None}, "bounty_amount", AMOUNT_MISSING),
    ({"bounty_type": "fixed", "bounty_amount": "abc"}, "bounty_amount", AMOUNT_MISSING),
    # `Job.bounty_amount` is numeric(15, 0): 16 digits, or a fraction of a dong, is a 400 with
    # one plain sentence about its size.
    ({"bounty_type": "fixed", "bounty_amount": 10**15}, "bounty_amount", AMOUNT_SIZE),
    ({"bounty_type": "fixed", "bounty_amount": "1000.5"}, "bounty_amount", AMOUNT_SIZE),
    ({"bounty_type": "fixed", "bounty_amount": "9" * 40}, "bounty_amount", AMOUNT_SIZE),
    ({}, "bounty_type", PICK_TYPE),
    ({"bounty_type": None}, "bounty_type", PICK_TYPE),
    ({"bounty_type": "percent"}, "bounty_type", PICK_TYPE),
]


# The queue --------------------------------------------------------------------------------


def test_queue_lists_pending_oldest_first_without_drafts_or_deleted(
    staff_client: APIClient,
) -> None:
    company = cast(Company, CompanyFactory())
    newest = queued_job(company, posted_ago_days=2)
    oldest = queued_job(company, posted_ago_days=9)
    middle = queued_job(company, posted_ago_days=5)
    # Not in the queue: still with the company, deleted, or already distributed.
    JobFactory(company=company, status=JobStatus.DRAFT, published_at=None)
    JobFactory(company=company, status=JobStatus.SUBMITTED, published_at=None)
    queued_job(company, posted_ago_days=30, deleted_at=timezone.now())
    JobFactory(company=company, status=JobStatus.ACTIVE)
    JobFactory(company=company, status=JobStatus.CLOSED)

    # `pending` is the default tab.
    body = staff_client.get(JOBS).json()

    assert body["count"] == 3
    assert [item["id"] for item in body["results"]] == [
        str(job.pk) for job in (oldest, middle, newest)
    ]
    assert set(body["results"][0]) == ITEM_KEYS
    assert staff_client.get(JOBS, {"status": ""}).json()["count"] == 3


@pytest.mark.parametrize("status", [JobStatus.ACTIVE, JobStatus.CLOSED])
def test_live_tabs_list_the_most_recently_distributed_first(
    staff_client: APIClient, status: JobStatus
) -> None:
    company = cast(Company, CompanyFactory())
    now = timezone.now()
    old, new, middle = (
        JobFactory(company=company, status=status, published_at=now - timedelta(days=days))
        for days in (20, 1, 8)
    )
    JobFactory(company=company, status=status, published_at=now - timedelta(days=2), deleted_at=now)
    queued_job(company, posted_ago_days=40)
    other = JobStatus.CLOSED if status == JobStatus.ACTIVE else JobStatus.ACTIVE
    JobFactory(company=company, status=other)

    body = staff_client.get(JOBS, {"status": status}).json()

    assert [item["id"] for item in body["results"]] == [str(job.pk) for job in (new, middle, old)]
    assert {item["status"] for item in body["results"]} == {status}


@pytest.mark.parametrize("status", ["draft", "submitted", "bogus"])
def test_queue_never_lists_private_or_unknown_statuses(
    staff_client: APIClient, status: str
) -> None:
    JobFactory(status=JobStatus.DRAFT, published_at=None)

    response = staff_client.get(JOBS, {"status": status})

    assert response.status_code == 400
    assert "status" in response.json()


def test_queue_searches_title_and_company_and_filters_by_company(staff_client: APIClient) -> None:
    vela = cast(Company, CompanyFactory(name="Vela Pay"))
    orbit = cast(Company, CompanyFactory(name="Orbit Labs"))
    payments = queued_job(vela, posted_ago_days=3, title="Payments Lead")
    data = queued_job(orbit, posted_ago_days=2, title="Data Engineer")
    platform = queued_job(orbit, posted_ago_days=1, title="Platform Engineer")

    def listed(**params: str) -> list[str]:
        return [item["id"] for item in staff_client.get(JOBS, params).json()["results"]]

    assert listed(search="payments") == [str(payments.pk)]
    assert listed(search="ORBIT") == [str(data.pk), str(platform.pk)]
    assert listed(search="engineer") == [str(data.pk), str(platform.pk)]
    assert listed(search="nothing like it") == []
    assert listed(company=str(orbit.pk)) == [str(data.pk), str(platform.pk)]
    assert listed(company=str(vela.pk), search="lead") == [str(payments.pk)]
    assert listed(company=str(uuid4())) == []
    assert staff_client.get(JOBS, {"company": "not-a-uuid"}).status_code == 400


def test_queue_is_paginated(staff_client: APIClient) -> None:
    company = cast(Company, CompanyFactory())
    jobs = [queued_job(company, posted_ago_days=days) for days in (4, 3, 2, 1)]

    first = staff_client.get(JOBS, {"page_size": 3}).json()
    second = staff_client.get(JOBS, {"page_size": 3, "page": 2}).json()

    assert first["count"] == 4
    assert [item["id"] for item in first["results"]] == [str(job.pk) for job in jobs[:3]]
    assert first["next"] is not None
    assert [item["id"] for item in second["results"]] == [str(jobs[3].pk)]


def test_queue_row_counts_working_recruiters(staff_client: APIClient) -> None:
    company = cast(Company, CompanyFactory())
    team = cast(Team, TeamFactory(company=company, name="Payments"))
    job = live_job(company=company, team=team, on_marketplace=False)
    JobAssignmentFactory.create_batch(2, job=job)
    JobAssignmentFactory(job=job, left_at=timezone.now())
    JobAssignmentFactory(job=live_job(company=company))

    body = staff_client.get(JOBS, {"status": "active"}).json()
    item = next(row for row in body["results"] if row["id"] == str(job.pk))

    assert item["team_name"] == "Payments"
    assert item["company"] == {"id": str(company.pk), "name": company.name}
    assert item["on_marketplace"] is False
    assert item["bounty_type"] == "pct"
    assert item["bounty_pct"] == 18
    assert item["recruiters_working"] == 2


def test_queue_queries_do_not_grow_with_the_page(
    staff_client: APIClient, django_assert_max_num_queries: Any
) -> None:
    company = cast(Company, CompanyFactory())
    for age in range(12):
        job = queued_job(company, posted_ago_days=age + 1, team=TeamFactory(company=company))
        JobAssignmentFactory(job=job)

    # Session, user, the count and the page; none of them per job.
    with django_assert_max_num_queries(4):
        assert len(staff_client.get(JOBS).json()["results"]) == 12


@pytest.mark.parametrize("who", ["anonymous", "company_admin", "recruiter_user"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", ""),
        ("get", "/{id}"),
        ("put", "/{id}/bounty"),
        ("post", "/{id}/distribute"),
        ("patch", "/{id}/channels"),
        ("post", "/{id}/assignments"),
        ("delete", "/{id}/assignments/{recruiter_id}"),
    ],
)
def test_ops_jobs_are_staff_only(
    who: str,
    method: str,
    path: str,
    api_client: APIClient,
    request: pytest.FixtureRequest,
) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)
    if who != "anonymous":
        api_client.force_login(request.getfixturevalue(who))

    response = getattr(api_client, method)(
        JOBS + path.format(id=job.pk, recruiter_id=uuid4()), {}, format="json"
    )

    assert response.status_code == 403
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING
    assert job.bounty_type is None


# One job -------------------------------------------------------------------------------------


def test_detail_shows_the_job(staff_client: APIClient) -> None:
    company = cast(Company, CompanyFactory(name="Vela Pay"))
    OfficeFactory(company=company)
    team = cast(Team, TeamFactory(company=company, name="Payments"))
    job = live_job(
        company=company,
        team=team,
        title="Payments Lead",
        note_for_recruiters="Send finance leaders first.",
        working_hours="40h_mon_fri",
        languages=["en", "vi"],
    )
    JobAssignmentFactory(job=job)

    response = staff_client.get(detail_url(job))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == DETAIL_KEYS
    assert body["title"] == "Payments Lead"
    assert body["company"] == {"id": str(company.pk), "name": "Vela Pay"}
    assert body["team_name"] == "Payments"
    assert body["status"] == "active"
    assert body["on_marketplace"] is True
    assert body["bounty_pct"] == 18
    assert body["note_for_recruiters"] == "Send finance leaders first."
    assert body["working_hours"] == "40h_mon_fri"
    assert body["languages"] == ["en", "vi"]
    assert body["recruiters_working"] == 1


def test_detail_has_every_jd_and_brief_field_the_marketplace_detail_shows(
    staff_client: APIClient,
) -> None:
    job = live_job()

    body = staff_client.get(detail_url(job)).json()

    assert set(MarketplaceJobSerializer().fields) - RECRUITER_ONLY <= set(body)


def test_detail_of_a_job_in_the_queue_has_no_bounty_yet(staff_client: APIClient) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1, team=None)

    body = staff_client.get(detail_url(job)).json()

    assert body["status"] == "pending"
    assert body["posted_at"] is not None
    assert body["published_at"] is None
    assert body["team_name"] is None
    assert body["bounty_type"] is None
    assert body["bounty_pct"] is None
    assert body["bounty_amount"] is None


@pytest.mark.parametrize(
    "fields",
    [
        {"status": JobStatus.DRAFT},
        {"status": JobStatus.SUBMITTED},
        {"status": JobStatus.ACTIVE, "deleted_at": timezone.now()},
        {"status": JobStatus.PENDING, "deleted_at": timezone.now()},
    ],
)
def test_detail_of_a_private_or_deleted_job_is_not_found(
    staff_client: APIClient, fields: dict[str, Any]
) -> None:
    job = JobFactory(published_at=None, **fields)

    assert staff_client.get(detail_url(job)).status_code == 404


def test_detail_of_an_unknown_job_is_not_found(staff_client: APIClient) -> None:
    assert staff_client.get(f"{JOBS}/{uuid4()}").status_code == 404


def test_detail_queries_do_not_grow_with_the_recruiters(
    staff_client: APIClient, django_assert_max_num_queries: Any
) -> None:
    job = live_job(team=TeamFactory())
    for _ in range(8):
        JobAssignmentFactory(job=job)

    # Session, user, the job, and its assignments with their recruiters; none per recruiter.
    with django_assert_max_num_queries(4):
        body = staff_client.get(detail_url(job)).json()
    assert len(body["assignments"]) == 8


def test_detail_lists_the_assignments_held_first_then_the_removed(
    staff_client: APIClient,
) -> None:
    job = live_job()
    now = timezone.now()
    first, second, third, gone = (make_recruiter() for _ in range(4))
    # Joined in this order; `gone` joined before all of them but has left.
    JobAssignmentFactory(job=job, recruiter=gone, left_at=now)
    for recruiter in (first, second, third):
        JobAssignmentFactory(job=job, recruiter=recruiter)
    JobAssignmentFactory(job=live_job())  # works on another job

    body = staff_client.get(detail_url(job)).json()

    assert [row["recruiter"]["id"] for row in body["assignments"]] == ids(
        [first, second, third, gone]
    )
    assert [row["left_at"] is None for row in body["assignments"]] == [True, True, True, False]
    assert body["assignments"][0]["recruiter"] == {
        "id": str(first.pk),
        "nickname": first.nickname,
        "email": first.user.email,
    }
    assert set(body["assignments"][0]) == {"recruiter", "joined_at", "left_at", "cv_count"}
    assert body["recruiters_working"] == 3


def test_detail_of_a_job_nobody_works_on_lists_no_assignments(staff_client: APIClient) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1, team=None)

    assert staff_client.get(detail_url(job)).json()["assignments"] == []


def test_assignment_row_counts_cvs(staff_client: APIClient) -> None:
    job = live_job()
    busy, idle = make_recruiter(), make_recruiter()
    for recruiter in (busy, idle):
        JobAssignmentFactory(job=job, recruiter=recruiter)
    ApplicationFactory.create_batch(2, job=job, recruiter=busy)
    # Not theirs, or not on this job: neither is counted.
    ApplicationFactory(job=job, recruiter=make_recruiter())
    ApplicationFactory(job=live_job(), recruiter=busy)

    rows = {
        row["recruiter"]["id"]: row["cv_count"]
        for row in staff_client.get(detail_url(job)).json()["assignments"]
    }

    assert rows == {str(busy.pk): 2, str(idle.pk): 0}


def keys_in(value: Any) -> set[str]:
    """Every key of every object in a JSON body, however deep."""
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in keys_in(item)}
    if isinstance(value, list):
        return {key for item in value for key in keys_in(item)}
    return set()


def test_job_payloads_have_no_invite_keys(staff_client: APIClient) -> None:
    job = live_job()
    JobAssignmentFactory(job=job)

    listed = staff_client.get(JOBS, {"status": "active"}).json()
    detail = staff_client.get(detail_url(job)).json()

    assert not {key for key in keys_in(listed) | keys_in(detail) if "invite" in key}


# Distribute --------------------------------------------------------------------------------


def test_distribute_puts_recruiters_on_the_job(
    staff_client: APIClient,
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Commit,
) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)
    first, second = make_recruiter(), make_recruiter()

    with django_capture_on_commit_callbacks(execute=True):
        response = distribute(staff_client, job, recruiter_ids=ids([first, second]))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == DETAIL_KEYS
    assert body["id"] == str(job.pk)
    assert body["status"] == "active"
    assert body["published_at"] is not None
    assert (body["bounty_type"], body["bounty_pct"], body["bounty_amount"]) == ("pct", 20, None)
    assert [row["recruiter"]["id"] for row in body["assignments"]] == ids([first, second])
    assert {row["left_at"] for row in body["assignments"]} == {None}
    assert body["recruiters_working"] == 2
    job.refresh_from_db()
    assert job.status == JobStatus.ACTIVE
    assert job.bounty_pct == 20
    assert held(job) == {first.pk, second.pk}
    # Each assigned recruiter hears about it once; nobody else does.
    assert sorted(message.to[0] for message in mailoutbox) == sorted(
        [first.user.email, second.user.email]
    )


def test_distribute_ignores_on_marketplace_while_it_is_off(staff_client: APIClient) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)

    response = distribute(staff_client, job, on_marketplace=True)

    assert response.status_code == 200
    assert response.json()["on_marketplace"] is False
    job.refresh_from_db()
    assert job.on_marketplace is False


@pytest.mark.usefixtures("marketplace_on")
@pytest.mark.parametrize(("sent", "stored"), [(True, True), (False, False), (None, False)])
def test_distribute_keeps_on_marketplace_when_it_is_enabled(
    staff_client: APIClient, sent: bool | None, stored: bool
) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)
    extra = {} if sent is None else {"on_marketplace": sent}

    response = distribute(staff_client, job, **extra)

    assert response.status_code == 200
    assert response.json()["on_marketplace"] is stored


def test_distribute_with_a_fixed_bounty_to_chosen_recruiters(
    staff_client: APIClient, django_capture_on_commit_callbacks: Commit
) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)
    recruiter = make_recruiter()

    with django_capture_on_commit_callbacks(execute=True):
        response = distribute(
            staff_client,
            job,
            bounty_type="fixed",
            bounty_pct=None,
            bounty_amount=12_000_000,
            recruiter_ids=ids([recruiter]),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["bounty_type"] == "fixed"
    assert body["bounty_pct"] is None
    assert body["on_marketplace"] is False
    job.refresh_from_db()
    assert job.bounty_amount == 12_000_000
    assert job.bounty_pct is None
    assert held(job) == {recruiter.pk}


def test_distribute_assigns_a_recruiter_listed_twice_once(
    staff_client: APIClient, django_capture_on_commit_callbacks: Commit, mailoutbox: list[Any]
) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)
    recruiter = make_recruiter()

    with django_capture_on_commit_callbacks(execute=True):
        response = distribute(staff_client, job, recruiter_ids=ids([recruiter, recruiter]))

    assert response.status_code == 200
    assert len(response.json()["assignments"]) == 1
    assert len(mailoutbox) == 1


def test_distribute_accepts_the_largest_amount_the_column_holds(staff_client: APIClient) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)

    response = distribute(staff_client, job, bounty_type="fixed", bounty_amount=999_999_999_999_999)

    assert response.status_code == 200
    job.refresh_from_db()
    assert job.bounty_amount == 999_999_999_999_999


@pytest.mark.parametrize(("payload", "field", "message"), BAD_BOUNTIES)
def test_distribute_rejects_bad_bounty(
    staff_client: APIClient, payload: dict[str, Any], field: str, message: str
) -> None:
    job = queued_job(
        cast(Company, CompanyFactory()), posted_ago_days=1, bounty_type="pct", bounty_pct=12
    )
    recruiter = make_recruiter()
    body = {"recruiter_ids": ids([recruiter]), **payload}

    response = staff_client.post(f"{detail_url(job)}/distribute", body, format="json")

    assert response.status_code == 400
    assert response.json() == {field: [message]}
    # Still waiting, bounty as it was, and nobody was assigned.
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING
    assert (job.bounty_type, job.bounty_pct, job.bounty_amount) == ("pct", 12, None)
    assert job.published_at is None
    assert not JobAssignment.objects.filter(job=job).exists()


def test_distribute_rejects_pct_31_as_the_brief_says(staff_client: APIClient) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)

    response = distribute(staff_client, job, bounty_pct=31)

    assert response.status_code == 400
    assert response.json() == {"bounty_pct": [PCT_RANGE]}


@pytest.mark.parametrize("payload", [{"recruiter_ids": []}, {"recruiter_ids": None}, None])
def test_distribute_without_recruiters_is_400(
    staff_client: APIClient, payload: dict[str, Any] | None
) -> None:
    """A job goes live with at least one recruiter; the bounty it was sent with is not kept."""
    job = queued_job(
        cast(Company, CompanyFactory()), posted_ago_days=1, bounty_type="pct", bounty_pct=12
    )
    body = {"bounty_type": "pct", "bounty_pct": 25}
    if payload is not None:
        body.update(payload)

    response = staff_client.post(f"{detail_url(job)}/distribute", body, format="json")

    assert response.status_code == 400
    expected = [NEEDS_RECRUITER]
    if payload == {"recruiter_ids": None}:
        expected = ["This field may not be null."]
    assert response.json() == {"recruiter_ids": expected}
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING
    assert job.bounty_pct == 12


@pytest.mark.parametrize(
    "fields",
    [
        {"status": JobStatus.CLOSED, "closed_at": timezone.now()},
        {"status": JobStatus.ACTIVE},
        {"status": JobStatus.PENDING, "deleted_at": timezone.now()},
        {"status": JobStatus.DRAFT, "published_at": None},
        {"status": JobStatus.SUBMITTED, "published_at": None},
    ],
    ids=["closed", "distributed_in_another_tab", "deleted", "draft", "submitted"],
)
def test_distribute_stale_job(staff_client: APIClient, fields: dict[str, Any]) -> None:
    """The page was stale: the job moved on. Nothing of the request is applied."""
    job = cast(Job, JobFactory(bounty_type="pct", bounty_pct=18, on_marketplace=False, **fields))
    before = Job.objects.values().get(pk=job.pk)
    recruiter = make_recruiter()

    response = distribute(
        staff_client, job, bounty_pct=25, on_marketplace=True, recruiter_ids=ids([recruiter])
    )

    assert response.status_code == 400
    assert response.json()["detail"] == NOT_WAITING
    after = Job.objects.values().get(pk=job.pk)
    after.pop("updated_at")
    before.pop("updated_at")
    assert after == before
    assert not JobAssignment.objects.filter(job=job).exists()


def test_distribute_unknown_job(staff_client: APIClient) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)
    response = staff_client.post(
        f"{JOBS}/{uuid4()}/distribute",
        {"bounty_type": "pct", "bounty_pct": 20, "recruiter_ids": ids([make_recruiter()])},
        format="json",
    )

    assert response.status_code == 404
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING


@pytest.mark.parametrize("kind", ["unknown", "pending", "rejected"])
def test_distribute_unknown_recruiter(staff_client: APIClient, kind: str) -> None:
    """Only an approved recruiter can be assigned, so any other is unknown to ops."""
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)
    good = make_recruiter()
    if kind == "unknown":
        bad = str(uuid4())
    else:
        bad = str(make_recruiter(status=RecruiterStatus(kind)).pk)

    response = distribute(staff_client, job, recruiter_ids=[str(good.pk), bad])

    assert response.status_code == 400
    assert response.json() == {"recruiter_ids": [UNKNOWN_RECRUITER]}
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING
    assert job.bounty_type is None
    assert not JobAssignment.objects.exists()


@pytest.mark.parametrize(
    "payload",
    [{"on_marketplace": "maybe"}, {"recruiter_ids": "not-a-list"}, {"recruiter_ids": ["nope"]}],
)
def test_distribute_rejects_malformed_input(
    staff_client: APIClient, payload: dict[str, Any]
) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)

    response = distribute(staff_client, job, **payload)

    assert response.status_code == 400
    assert set(response.json()) <= {"on_marketplace", "recruiter_ids"}
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING


# Bounty ----------------------------------------------------------------------------------------


def test_bounty_update_on_active_job(staff_client: APIClient) -> None:
    recruiter_job = live_job()
    JobAssignmentFactory(job=recruiter_job)

    response = put_bounty(
        staff_client, recruiter_job, {"bounty_type": "fixed", "bounty_amount": 8_000_000}
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == DETAIL_KEYS
    assert (body["bounty_type"], body["bounty_pct"]) == ("fixed", None)
    assert body["status"] == "active"
    recruiter_job.refresh_from_db()
    assert recruiter_job.bounty_amount == 8_000_000
    assert recruiter_job.bounty_pct is None
    assert JobAssignment.objects.filter(job=recruiter_job).count() == 1

    back = put_bounty(
        staff_client, recruiter_job, {"bounty_type": "pct", "bounty_pct": 25, "bounty_amount": 5}
    )

    assert back.status_code == 200
    recruiter_job.refresh_from_db()
    assert (recruiter_job.bounty_type, recruiter_job.bounty_pct) == ("pct", 25)
    # The amount the pct bounty does not use is dropped, not kept.
    assert recruiter_job.bounty_amount is None


def test_bounty_update_of_a_job_in_the_queue_keeps_it_waiting(staff_client: APIClient) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)

    response = put_bounty(staff_client, job, {"bounty_type": "pct", "bounty_pct": 15})

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING
    assert job.bounty_pct == 15
    assert job.published_at is None


def test_bounty_update_of_a_closed_job(staff_client: APIClient) -> None:
    job = live_job(status=JobStatus.CLOSED, closed_at=timezone.now())

    response = put_bounty(staff_client, job, {"bounty_type": "pct", "bounty_pct": 30})

    assert response.status_code == 200
    job.refresh_from_db()
    assert job.status == JobStatus.CLOSED
    assert job.bounty_pct == 30


@pytest.mark.parametrize(("payload", "field", "message"), BAD_BOUNTIES)
def test_bounty_update_rejects_bad_bounty(
    staff_client: APIClient, payload: dict[str, Any], field: str, message: str
) -> None:
    job = live_job()

    response = put_bounty(staff_client, job, payload)

    assert response.status_code == 400
    assert response.json() == {field: [message]}
    job.refresh_from_db()
    assert (job.bounty_type, job.bounty_pct, job.bounty_amount) == ("pct", 18, None)


def test_bounty_update_accepts_the_largest_amount_the_column_holds(
    staff_client: APIClient,
) -> None:
    job = live_job()

    response = put_bounty(
        staff_client, job, {"bounty_type": "fixed", "bounty_amount": 999_999_999_999_999}
    )

    assert response.status_code == 200
    job.refresh_from_db()
    assert job.bounty_amount == 999_999_999_999_999


@pytest.mark.parametrize(
    ("sent", "stored"),
    [(5, 5), (30, 30), (18, 18), (18.0, 18), ("25", 25), ("25.0", 25)],
)
def test_bounty_update_stores_a_whole_number_pct_as_an_int(
    staff_client: APIClient, sent: Any, stored: int
) -> None:
    job = live_job(bounty_type="fixed", bounty_pct=None, bounty_amount=5_000_000)

    response = put_bounty(staff_client, job, {"bounty_type": "pct", "bounty_pct": sent})

    assert response.status_code == 200
    assert response.json()["bounty_pct"] == stored
    job.refresh_from_db()
    assert job.bounty_pct == stored
    assert isinstance(job.bounty_pct, int)
    assert job.bounty_amount is None


@pytest.mark.parametrize(
    "fields",
    [
        {"status": JobStatus.DRAFT, "published_at": None},
        {"status": JobStatus.SUBMITTED, "published_at": None},
        {"status": JobStatus.ACTIVE, "deleted_at": timezone.now()},
    ],
)
def test_bounty_update_of_a_private_or_deleted_job_is_not_found(
    staff_client: APIClient, fields: dict[str, Any]
) -> None:
    job = JobFactory(**fields)

    response = put_bounty(staff_client, job, {"bounty_type": "pct", "bounty_pct": 10})

    assert response.status_code == 404
    job.refresh_from_db()
    assert job.bounty_pct == 18


def test_bounty_update_of_an_unknown_job(staff_client: APIClient) -> None:
    response = staff_client.put(
        f"{JOBS}/{uuid4()}/bounty", {"bounty_type": "pct", "bounty_pct": 10}, format="json"
    )

    assert response.status_code == 404


# Assignments -----------------------------------------------------------------------------------


def test_assign_more_recruiters(
    staff_client: APIClient,
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Commit,
) -> None:
    job = live_job()
    already = make_recruiter()
    JobAssignmentFactory(job=job, recruiter=already)
    first, second = make_recruiter(), make_recruiter()

    with django_capture_on_commit_callbacks(execute=True):
        response = assign(staff_client, job, ids([first, second]))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == DETAIL_KEYS
    assert [row["recruiter"]["id"] for row in body["assignments"]] == ids([already, first, second])
    assert {row["left_at"] for row in body["assignments"]} == {None}
    assert body["recruiters_working"] == 3
    assert sorted(message.to[0] for message in mailoutbox) == sorted(
        [first.user.email, second.user.email]
    )


def test_assign_leaves_one_already_on_the_job_alone(
    staff_client: APIClient,
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Commit,
) -> None:
    job = live_job()
    already = make_recruiter()
    JobAssignmentFactory(job=job, recruiter=already)

    with django_capture_on_commit_callbacks(execute=True):
        response = assign(staff_client, job, ids([already, already]))

    assert response.status_code == 200
    assert len(response.json()["assignments"]) == 1
    assert mailoutbox == []


def test_assign_puts_a_removed_recruiter_back(staff_client: APIClient) -> None:
    job = live_job()
    stays, returns = make_recruiter(), make_recruiter()
    JobAssignmentFactory(job=job, recruiter=stays)
    JobAssignmentFactory(job=job, recruiter=returns, left_at=timezone.now())

    response = assign(staff_client, job, ids([returns]))

    assert response.status_code == 200
    assert held(job) == {stays.pk, returns.pk}
    assert JobAssignment.objects.filter(job=job).count() == 2


@pytest.mark.parametrize("case", ["marketplace_only", "last_one_removed"])
def test_assign_to_a_live_job_nobody_works_on(staff_client: APIClient, case: str) -> None:
    """Both are real: a legacy marketplace-only job, and one reopened after its last removal."""
    job = live_job(on_marketplace=case == "marketplace_only")
    if case == "last_one_removed":
        JobAssignmentFactory(job=job, left_at=timezone.now())
    recruiter = make_recruiter()

    response = assign(staff_client, job, ids([recruiter]))

    assert response.status_code == 200
    assert response.json()["recruiters_working"] == 1
    assert held(job) == {recruiter.pk}


@pytest.mark.parametrize("recruiter_ids", [[], None])
def test_assign_needs_a_recruiter(staff_client: APIClient, recruiter_ids: list[str] | None) -> None:
    job = live_job()
    body = {} if recruiter_ids is None else {"recruiter_ids": recruiter_ids}

    response = staff_client.post(f"{detail_url(job)}/assignments", body, format="json")

    assert response.status_code == 400
    assert response.json() == {"recruiter_ids": [NEEDS_RECRUITER]}


@pytest.mark.parametrize("kind", ["unknown", "pending", "rejected"])
def test_assign_unknown_recruiter(staff_client: APIClient, kind: str) -> None:
    job = live_job()
    good = make_recruiter()
    bad = (
        str(uuid4()) if kind == "unknown" else str(make_recruiter(status=RecruiterStatus(kind)).pk)
    )

    response = assign(staff_client, job, [str(good.pk), bad])

    assert response.status_code == 400
    assert response.json() == {"recruiter_ids": [UNKNOWN_RECRUITER]}
    assert not JobAssignment.objects.exists()


@pytest.mark.parametrize("status", [JobStatus.PENDING, JobStatus.CLOSED])
def test_assign_needs_a_live_job(staff_client: APIClient, status: JobStatus) -> None:
    job = live_job(status=status)
    recruiter = make_recruiter()

    response = assign(staff_client, job, ids([recruiter]))

    assert response.status_code == 400
    assert response.json()["detail"] == NOT_LIVE
    assert not JobAssignment.objects.exists()


def test_assign_to_a_private_deleted_or_unknown_job_is_not_found(staff_client: APIClient) -> None:
    recruiter = make_recruiter()
    private = JobFactory(status=JobStatus.DRAFT, published_at=None)
    deleted = live_job(deleted_at=timezone.now())

    assert assign(staff_client, private, ids([recruiter])).status_code == 404
    assert assign(staff_client, deleted, ids([recruiter])).status_code == 404
    unknown = staff_client.post(
        f"{JOBS}/{uuid4()}/assignments", {"recruiter_ids": ids([recruiter])}, format="json"
    )
    assert unknown.status_code == 404
    assert not JobAssignment.objects.exists()


def test_remove_recruiter(staff_client: APIClient) -> None:
    job = live_job()
    leaving, staying = make_recruiter(), make_recruiter()
    JobAssignmentFactory(job=job, recruiter=leaving)
    JobAssignmentFactory(job=job, recruiter=staying)
    ApplicationFactory(job=job, recruiter=leaving)

    response = remove(staff_client, job, leaving)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == DETAIL_KEYS
    assert body["recruiters_working"] == 1
    # The one still on the job first, then the one taken off, who keeps their CV count.
    assert [row["recruiter"]["id"] for row in body["assignments"]] == ids([staying, leaving])
    assert body["assignments"][0]["left_at"] is None
    assert body["assignments"][1]["left_at"] is not None
    assert body["assignments"][1]["cv_count"] == 1
    assert held(job) == {staying.pk}


def test_remove_does_not_email_the_recruiter(
    staff_client: APIClient,
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Commit,
) -> None:
    job = live_job()
    leaving = make_recruiter()
    JobAssignmentFactory(job=job, recruiter=leaving)
    JobAssignmentFactory(job=job)

    with django_capture_on_commit_callbacks(execute=True):
        assert remove(staff_client, job, leaving).status_code == 200

    assert mailoutbox == []


def test_remove_last_recruiter_is_400(staff_client: APIClient) -> None:
    job = live_job()
    only = make_recruiter()
    JobAssignmentFactory(job=job, recruiter=only)

    response = remove(staff_client, job, only)

    assert response.status_code == 400
    assert response.json()["detail"] == LAST_RECRUITER
    assert held(job) == {only.pk}


@pytest.mark.parametrize("case", ["never_on_it", "already_removed", "on_another_job"])
def test_remove_someone_not_on_the_job_is_not_found(staff_client: APIClient, case: str) -> None:
    job = live_job()
    JobAssignmentFactory(job=job)
    recruiter = make_recruiter()
    if case == "already_removed":
        JobAssignmentFactory(job=job, recruiter=recruiter, left_at=timezone.now())
    if case == "on_another_job":
        JobAssignmentFactory(job=live_job(), recruiter=recruiter)

    assert remove(staff_client, job, recruiter).status_code == 404


def test_remove_from_a_private_or_unknown_job_is_not_found(staff_client: APIClient) -> None:
    private = JobFactory(status=JobStatus.DRAFT, published_at=None)
    recruiter = make_recruiter()
    JobAssignmentFactory(job=private, recruiter=recruiter)

    assert remove(staff_client, private, recruiter).status_code == 404
    unknown = staff_client.delete(f"{JOBS}/{uuid4()}/assignments/{recruiter.pk}")
    assert unknown.status_code == 404
    assert held(private) == {recruiter.pk}


@pytest.mark.parametrize("who", ["anonymous", "company_admin", "recruiter_user"])
def test_non_staff_cannot_assign_or_remove(
    who: str, api_client: APIClient, request: pytest.FixtureRequest
) -> None:
    job = live_job()
    on_it = make_recruiter()
    JobAssignmentFactory(job=job, recruiter=on_it)
    JobAssignmentFactory(job=job)
    newcomer = make_recruiter()
    if who != "anonymous":
        api_client.force_login(request.getfixturevalue(who))

    added = assign(api_client, job, ids([newcomer]))
    removed = remove(api_client, job, on_it)

    assert added.status_code == 403
    assert removed.status_code == 403
    assert newcomer.pk not in held(job)
    assert on_it.pk in held(job)


# Unstaffed jobs --------------------------------------------------------------------------------


def test_unstaffed_filter_and_count(staff_client: APIClient) -> None:
    company = cast(Company, CompanyFactory())
    unstaffed = live_job(company=company, on_marketplace=True)
    reopened = live_job(company=company)
    JobAssignmentFactory(job=reopened, left_at=timezone.now())
    staffed = live_job(company=company)
    JobAssignmentFactory(job=staffed)
    # Not counted: closed, deleted.
    live_job(company=company, status=JobStatus.CLOSED, closed_at=timezone.now())
    live_job(company=company, deleted_at=timezone.now())

    def listed(**params: str) -> set[str]:
        body = staff_client.get(JOBS, {"status": "active", **params}).json()
        return {item["id"] for item in body["results"]}

    assert listed(unstaffed="true") == {str(unstaffed.pk), str(reopened.pk)}
    assert listed(unstaffed="false") == {str(staffed.pk)}
    assert listed() == {str(unstaffed.pk), str(reopened.pk), str(staffed.pk)}
    assert staff_client.get("/api/v1/ops/overview").json()["unstaffed_jobs"] == 2


# Marketplace switch ----------------------------------------------------------------------------


def test_channels_endpoint_404_when_marketplace_off(staff_client: APIClient) -> None:
    job = live_job(on_marketplace=False)

    assert patch_channels(staff_client, job, {"on_marketplace": True}).status_code == 404
    job.refresh_from_db()
    assert job.on_marketplace is False


@pytest.mark.usefixtures("marketplace_on")
def test_channels_take_a_job_off_the_marketplace(staff_client: APIClient) -> None:
    job = live_job(on_marketplace=True)
    recruiter = make_recruiter()
    JobAssignmentFactory(job=job, recruiter=recruiter)

    response = patch_channels(staff_client, job, {"on_marketplace": False})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == DETAIL_KEYS
    assert body["on_marketplace"] is False
    assert held(job) == {recruiter.pk}


@pytest.mark.usefixtures("marketplace_on")
def test_channels_turn_the_marketplace_back_on(staff_client: APIClient) -> None:
    job = live_job(on_marketplace=False)

    response = patch_channels(staff_client, job, {"on_marketplace": True})

    assert response.status_code == 200
    assert response.json()["on_marketplace"] is True
    job.refresh_from_db()
    assert job.on_marketplace is True


@pytest.mark.usefixtures("marketplace_on")
def test_channels_of_a_job_in_the_queue_are_not_open_yet(staff_client: APIClient) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)

    response = patch_channels(staff_client, job, {"on_marketplace": True})

    assert response.status_code == 400
    assert response.json()["detail"] == NOT_DISTRIBUTED
    job.refresh_from_db()
    assert job.on_marketplace is False


@pytest.mark.usefixtures("marketplace_on")
def test_channels_of_a_closed_job_take_the_marketplace_switch(staff_client: APIClient) -> None:
    job = live_job(status=JobStatus.CLOSED, closed_at=timezone.now(), on_marketplace=True)

    response = patch_channels(staff_client, job, {"on_marketplace": False})

    assert response.status_code == 200
    assert response.json()["on_marketplace"] is False


@pytest.mark.usefixtures("marketplace_on")
@pytest.mark.parametrize("payload", [{"on_marketplace": "maybe"}, {}])
def test_channels_reject_malformed_input(staff_client: APIClient, payload: dict[str, Any]) -> None:
    job = live_job()

    response = patch_channels(staff_client, job, payload)

    assert response.status_code == 400
    assert set(response.json()) == {"on_marketplace"}


@pytest.mark.usefixtures("marketplace_on")
@pytest.mark.parametrize(
    "fields",
    [
        {"status": JobStatus.DRAFT, "published_at": None},
        {"status": JobStatus.ACTIVE, "deleted_at": timezone.now()},
    ],
)
def test_channels_of_a_private_or_deleted_job_are_not_found(
    staff_client: APIClient, fields: dict[str, Any]
) -> None:
    job = JobFactory(**fields)

    assert patch_channels(staff_client, job, {"on_marketplace": False}).status_code == 404
    assert (
        staff_client.patch(
            f"{JOBS}/{uuid4()}/channels", {"on_marketplace": False}, format="json"
        ).status_code
        == 404
    )
