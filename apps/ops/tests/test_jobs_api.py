"""Sparktal ops: the job queue, one job, its bounty, its distribution and its channels."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from django.test.client import encode_multipart
from django.utils import timezone
from django.utils.http import urlencode
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company, Team
from apps.companies.tests.factories import CompanyFactory, OfficeFactory, TeamFactory
from apps.jobs.models import InviteStatus, Job, JobAssignment, JobInvite, JobStatus
from apps.jobs.serializers import MarketplaceJobSerializer
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory, JobInviteFactory
from apps.ops.tests.helpers import ITEM_KEYS, queued_job
from apps.recruiters.models import RecruiterProfile, RecruiterStatus
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

JOBS = "/api/v1/ops/jobs"
NOT_WAITING = "This job is no longer waiting for distribution."
NEEDS_CHANNEL = "Pick the marketplace, at least one recruiter, or both."
NEEDS_BOUNTY = "Set the bounty before distributing."
NOT_DISTRIBUTED = "This job hasn't been distributed yet."
NOT_LIVE = "Only a live job can take invites."
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
    "invites",
}
INVITE_KEYS = {"id", "recruiter", "status", "decline_reason", "created_at", "responded_at"}
# On the marketplace detail only because it is a recruiter looking at the job.
RECRUITER_ONLY = {"joined", "submitted_count", "channel_id", "invite", "created_at"}

Commit = Callable[..., Any]


def detail_url(job: Job) -> str:
    return f"{JOBS}/{job.pk}"


def distribute(client: APIClient, job: Job, **payload: Any) -> Any:
    body = {
        "bounty_type": "pct",
        "bounty_pct": 20,
        "on_marketplace": True,
        "recruiter_ids": [],
        **payload,
    }
    return client.post(f"{detail_url(job)}/distribute", body, format="json")


def put_bounty(client: APIClient, job: Job, payload: dict[str, Any]) -> Any:
    return client.put(f"{detail_url(job)}/bounty", payload, format="json")


def patch_channels(client: APIClient, job: Job, payload: dict[str, Any]) -> Any:
    return client.patch(f"{detail_url(job)}/channels", payload, format="json")


def make_recruiter(**fields: Any) -> RecruiterProfile:
    return cast(RecruiterProfile, RecruiterProfileFactory(**fields))


def ids(recruiters: list[RecruiterProfile]) -> list[str]:
    return [str(recruiter.pk) for recruiter in recruiters]


def live_job(**fields: Any) -> Job:
    """A job Sparktal already distributed: active, 18%, on the marketplace."""
    return cast(Job, JobFactory(**{"status": JobStatus.ACTIVE, **fields}))


def invite_status(job: Job) -> dict[Any, str]:
    return {invite.recruiter_id: invite.status for invite in JobInvite.objects.filter(job=job)}


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


def test_queue_row_counts_invites_and_working_recruiters(staff_client: APIClient) -> None:
    company = cast(Company, CompanyFactory())
    team = cast(Team, TeamFactory(company=company, name="Payments"))
    job = live_job(company=company, team=team, on_marketplace=False)
    # Three open invites against two recruiters on the job: a join of both would give six.
    JobInviteFactory.create_batch(3, job=job, status=InviteStatus.PENDING)
    JobInviteFactory(job=job, status=InviteStatus.ACCEPTED)
    JobInviteFactory(job=job, status=InviteStatus.DECLINED)
    JobInviteFactory(job=job, status=InviteStatus.CANCELLED)
    JobAssignmentFactory.create_batch(2, job=job)
    JobAssignmentFactory(job=job, left_at=timezone.now())
    JobInviteFactory(job=live_job(company=company), status=InviteStatus.PENDING)

    body = staff_client.get(JOBS, {"status": "active"}).json()
    item = next(row for row in body["results"] if row["id"] == str(job.pk))

    assert item["team_name"] == "Payments"
    assert item["company"] == {"id": str(company.pk), "name": company.name}
    assert item["on_marketplace"] is False
    assert item["bounty_type"] == "pct"
    assert item["bounty_pct"] == 18
    assert item["invites"] == {"pending": 3, "accepted": 1, "declined": 1}
    assert item["recruiters_working"] == 2


def test_queue_queries_do_not_grow_with_the_page(
    staff_client: APIClient, django_assert_max_num_queries: Any
) -> None:
    company = cast(Company, CompanyFactory())
    for age in range(12):
        job = queued_job(company, posted_ago_days=age + 1, team=TeamFactory(company=company))
        JobInviteFactory(job=job)
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

    response = getattr(api_client, method)(JOBS + path.format(id=job.pk), {}, format="json")

    assert response.status_code == 403
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING
    assert job.bounty_type is None


# One job -------------------------------------------------------------------------------------


def test_detail_shows_invites_with_decline_reason(staff_client: APIClient) -> None:
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
    declined = JobInviteFactory(
        job=job,
        recruiter=make_recruiter(nickname="Bluefin Search", user__email="blue@fin.vn"),
        status=InviteStatus.DECLINED,
        decline_reason="Too far outside my niche",
        responded_at=timezone.now(),
    )
    pending = JobInviteFactory(job=job, status=InviteStatus.PENDING)
    cancelled = JobInviteFactory(job=job, status=InviteStatus.CANCELLED)
    now = timezone.now()
    for age, invite in enumerate((pending, declined, cancelled)):
        JobInvite.objects.filter(pk=invite.pk).update(created_at=now - timedelta(days=age))
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
    # Every invite, newest first: what became of each is the point of the panel.
    assert [invite["id"] for invite in body["invites"]] == [
        str(pending.pk),
        str(declined.pk),
        str(cancelled.pk),
    ]
    shown = body["invites"][1]
    assert set(shown) == INVITE_KEYS
    assert shown["recruiter"] == {
        "id": str(declined.recruiter_id),
        "nickname": "Bluefin Search",
        "email": "blue@fin.vn",
    }
    assert shown["status"] == "declined"
    assert shown["decline_reason"] == "Too far outside my niche"
    assert shown["responded_at"] is not None
    assert body["invites"][0]["responded_at"] is None
    assert body["invites"][0]["decline_reason"] == ""


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
    assert body["invites"] == []


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


def test_detail_queries_do_not_grow_with_the_invites(
    staff_client: APIClient, django_assert_max_num_queries: Any
) -> None:
    job = live_job(team=TeamFactory())
    for _ in range(8):
        JobInviteFactory(job=job)
        JobAssignmentFactory(job=job)

    # Session, user, the job, its invites with their recruiters; none of them per invite.
    with django_assert_max_num_queries(4):
        assert len(staff_client.get(detail_url(job)).json()["invites"]) == 8


# Distribute --------------------------------------------------------------------------------


def test_distribute_sets_bounty_and_channels(
    staff_client: APIClient,
    staff_user: User,
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
    assert body["on_marketplace"] is True
    assert (body["bounty_type"], body["bounty_pct"], body["bounty_amount"]) == ("pct", 20, None)
    assert {invite["recruiter"]["id"] for invite in body["invites"]} == set(ids([first, second]))
    assert {invite["status"] for invite in body["invites"]} == {"pending"}
    job.refresh_from_db()
    assert job.status == JobStatus.ACTIVE
    assert job.bounty_pct == 20
    assert job.on_marketplace is True
    assert {invite.invited_by for invite in JobInvite.objects.filter(job=job)} == {staff_user}
    # Each invited recruiter hears about it once; nobody else does.
    assert sorted(message.to[0] for message in mailoutbox) == sorted(
        [first.user.email, second.user.email]
    )


def test_distribute_with_a_fixed_bounty_to_invited_recruiters_only(
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
            on_marketplace=False,
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
    assert invite_status(job) == {recruiter.pk: "pending"}


def test_distribute_to_the_marketplace_alone_sends_no_invites(
    staff_client: APIClient, mailoutbox: list[Any], django_capture_on_commit_callbacks: Commit
) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)

    with django_capture_on_commit_callbacks(execute=True):
        response = distribute(staff_client, job)

    assert response.status_code == 200
    assert response.json()["invites"] == []
    assert mailoutbox == []


def test_distribute_invites_a_recruiter_listed_twice_once(
    staff_client: APIClient, django_capture_on_commit_callbacks: Commit, mailoutbox: list[Any]
) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)
    recruiter = make_recruiter()

    with django_capture_on_commit_callbacks(execute=True):
        response = distribute(staff_client, job, recruiter_ids=ids([recruiter, recruiter]))

    assert response.status_code == 200
    assert len(response.json()["invites"]) == 1
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
    body = {"on_marketplace": True, "recruiter_ids": ids([recruiter]), **payload}

    response = staff_client.post(f"{detail_url(job)}/distribute", body, format="json")

    assert response.status_code == 400
    assert response.json() == {field: [message]}
    # Still waiting, bounty as it was, and nobody was invited.
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING
    assert (job.bounty_type, job.bounty_pct, job.bounty_amount) == ("pct", 12, None)
    assert job.published_at is None
    assert not JobInvite.objects.filter(job=job).exists()


def test_distribute_rejects_pct_31_as_the_brief_says(staff_client: APIClient) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)

    response = distribute(staff_client, job, bounty_pct=31)

    assert response.status_code == 400
    assert response.json() == {"bounty_pct": [PCT_RANGE]}


def test_distribute_without_a_channel_leaves_the_bounty_as_it_was(
    staff_client: APIClient,
) -> None:
    """The bounty and the distribution are one transaction: a refused channel undoes the bounty."""
    job = queued_job(
        cast(Company, CompanyFactory()), posted_ago_days=1, bounty_type="pct", bounty_pct=12
    )

    response = distribute(staff_client, job, bounty_pct=25, on_marketplace=False)

    assert response.status_code == 400
    assert response.json()["detail"] == NEEDS_CHANNEL
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
    assert not JobInvite.objects.filter(job=job).exists()


def test_distribute_unknown_job(staff_client: APIClient) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)
    response = staff_client.post(
        f"{JOBS}/{uuid4()}/distribute",
        {"bounty_type": "pct", "bounty_pct": 20, "on_marketplace": True, "recruiter_ids": []},
        format="json",
    )

    assert response.status_code == 404
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING


@pytest.mark.parametrize("kind", ["unknown", "pending", "rejected"])
def test_distribute_unknown_recruiter(staff_client: APIClient, kind: str) -> None:
    """Only an approved recruiter can be invited, so any other is unknown to ops."""
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
    assert not JobInvite.objects.exists()


@pytest.mark.parametrize(
    "payload",
    [{"on_marketplace": "maybe"}, {"recruiter_ids": "not-a-list"}, {"recruiter_ids": ["nope"]}],
)
def test_distribute_rejects_malformed_channels(
    staff_client: APIClient, payload: dict[str, Any]
) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)

    response = distribute(staff_client, job, **payload)

    assert response.status_code == 400
    assert set(response.json()) <= {"on_marketplace", "recruiter_ids"}
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING


def test_distribute_needs_both_channel_fields(staff_client: APIClient) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)

    response = staff_client.post(
        f"{detail_url(job)}/distribute", {"bounty_type": "pct", "bounty_pct": 20}, format="json"
    )

    assert response.status_code == 400
    assert set(response.json()) == {"on_marketplace", "recruiter_ids"}


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


# Channels --------------------------------------------------------------------------------------


def test_channels_toggle_invite_and_cancel(
    staff_client: APIClient,
    staff_user: User,
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Commit,
) -> None:
    job = live_job(on_marketplace=True)
    waiting = JobInviteFactory(job=job, status=InviteStatus.PENDING)
    answered = JobInviteFactory(job=job, status=InviteStatus.ACCEPTED)
    newcomer = make_recruiter()

    with django_capture_on_commit_callbacks(execute=True):
        response = patch_channels(
            staff_client,
            job,
            {
                "on_marketplace": False,
                "invite_recruiter_ids": ids([newcomer]),
                "cancel_invite_ids": [str(waiting.pk), str(answered.pk)],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == DETAIL_KEYS
    assert body["on_marketplace"] is False
    assert body["status"] == "active"
    assert invite_status(job) == {
        waiting.recruiter_id: "cancelled",
        # Only an invite nobody answered can be withdrawn.
        answered.recruiter_id: "accepted",
        newcomer.pk: "pending",
    }
    assert JobInvite.objects.get(job=job, recruiter=newcomer).invited_by == staff_user
    assert [message.to for message in mailoutbox] == [[newcomer.user.email]]


def test_channels_turn_the_marketplace_back_on(staff_client: APIClient) -> None:
    job = live_job(on_marketplace=False)

    response = patch_channels(staff_client, job, {"on_marketplace": True})

    assert response.status_code == 200
    assert response.json()["on_marketplace"] is True
    job.refresh_from_db()
    assert job.on_marketplace is True


def test_channels_invite_again_a_recruiter_who_declined(
    staff_client: APIClient, django_capture_on_commit_callbacks: Commit, mailoutbox: list[Any]
) -> None:
    job = live_job()
    declined = JobInviteFactory(
        job=job,
        status=InviteStatus.DECLINED,
        decline_reason="Busy",
        responded_at=timezone.now(),
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = patch_channels(
            staff_client, job, {"invite_recruiter_ids": [str(declined.recruiter_id)]}
        )

    assert response.status_code == 200
    declined.refresh_from_db()
    assert declined.status == InviteStatus.PENDING
    assert declined.decline_reason == ""
    assert declined.responded_at is None
    assert len(mailoutbox) == 1
    assert response.json()["invites"][0]["status"] == "pending"


def test_channels_cancel_ignores_invites_of_other_jobs(staff_client: APIClient) -> None:
    job = live_job()
    other = JobInviteFactory(job=live_job(), status=InviteStatus.PENDING)

    response = patch_channels(staff_client, job, {"cancel_invite_ids": [str(other.pk)]})

    assert response.status_code == 200
    other.refresh_from_db()
    assert other.status == InviteStatus.PENDING


@pytest.mark.parametrize("kind", ["unknown", "pending", "rejected"])
def test_channels_unknown_recruiter(staff_client: APIClient, kind: str) -> None:
    job = live_job(on_marketplace=True)
    good = make_recruiter()
    if kind == "unknown":
        bad = str(uuid4())
    else:
        bad = str(make_recruiter(status=RecruiterStatus(kind)).pk)

    response = patch_channels(
        staff_client,
        job,
        {"on_marketplace": False, "invite_recruiter_ids": [str(good.pk), bad]},
    )

    # The brief keys this by `recruiter_ids` for every list of recruiters, this one included.
    assert response.status_code == 400
    assert response.json() == {"recruiter_ids": [UNKNOWN_RECRUITER]}
    job.refresh_from_db()
    assert job.on_marketplace is True
    assert not JobInvite.objects.exists()


def test_channels_of_a_job_in_the_queue_are_not_open_yet(staff_client: APIClient) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)
    recruiter = make_recruiter()

    marketplace = patch_channels(staff_client, job, {"on_marketplace": True})
    invite = patch_channels(staff_client, job, {"invite_recruiter_ids": ids([recruiter])})

    assert marketplace.status_code == 400
    assert marketplace.json()["detail"] == NOT_DISTRIBUTED
    assert invite.status_code == 400
    assert invite.json()["detail"] == NOT_LIVE
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING
    assert job.on_marketplace is False
    assert not JobInvite.objects.exists()


def test_channels_of_a_closed_job_take_the_marketplace_but_no_invites(
    staff_client: APIClient,
) -> None:
    job = live_job(status=JobStatus.CLOSED, closed_at=timezone.now(), on_marketplace=True)
    recruiter = make_recruiter()

    switched = patch_channels(staff_client, job, {"on_marketplace": False})
    invited = patch_channels(staff_client, job, {"invite_recruiter_ids": ids([recruiter])})

    assert switched.status_code == 200
    assert switched.json()["on_marketplace"] is False
    assert invited.status_code == 400
    assert invited.json()["detail"] == NOT_LIVE
    assert not JobInvite.objects.exists()


def test_channels_change_is_all_or_nothing(staff_client: APIClient) -> None:
    """A legacy live job without a bounty cannot take invites; the marketplace switch is undone."""
    job = live_job(bounty_type=None, bounty_pct=None, on_marketplace=True)
    recruiter = make_recruiter()

    response = patch_channels(
        staff_client,
        job,
        {"on_marketplace": False, "invite_recruiter_ids": ids([recruiter])},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == NEEDS_BOUNTY
    job.refresh_from_db()
    assert job.on_marketplace is True


@pytest.mark.parametrize("encoding", ["multipart", "urlencoded"])
def test_channels_form_encoded_invite_keeps_the_marketplace_on(
    staff_client: APIClient,
    encoding: str,
    django_capture_on_commit_callbacks: Commit,
    mailoutbox: list[Any],
) -> None:
    """A form body has no "no value": an absent `on_marketplace` must not read as False."""
    job = live_job(on_marketplace=True)
    newcomer, other = make_recruiter(), make_recruiter()
    body = {"invite_recruiter_ids": ids([newcomer, other])}
    content_type = "multipart/form-data; boundary=BoUnDaRy"
    if encoding == "urlencoded":
        content_type = "application/x-www-form-urlencoded"
    data = (
        encode_multipart("BoUnDaRy", body)
        if encoding == "multipart"
        else urlencode(body, doseq=True)
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = staff_client.generic(
            "PATCH", f"{detail_url(job)}/channels", data, content_type=content_type
        )

    assert response.status_code == 200
    assert response.json()["on_marketplace"] is True
    job.refresh_from_db()
    assert job.on_marketplace is True
    assert invite_status(job) == {newcomer.pk: "pending", other.pk: "pending"}
    assert len(mailoutbox) == 2


def test_channels_with_nothing_to_change(staff_client: APIClient) -> None:
    job = live_job(on_marketplace=True)

    response = patch_channels(staff_client, job, {})

    assert response.status_code == 200
    assert response.json()["on_marketplace"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"on_marketplace": "maybe"},
        {"invite_recruiter_ids": "not-a-list"},
        {"cancel_invite_ids": ["nope"]},
    ],
)
def test_channels_reject_malformed_input(staff_client: APIClient, payload: dict[str, Any]) -> None:
    job = live_job()

    response = patch_channels(staff_client, job, payload)

    assert response.status_code == 400
    assert set(response.json()) == set(payload)


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
