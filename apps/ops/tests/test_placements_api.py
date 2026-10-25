"""Sparktal ops: the placements list, marking one paid, and the guarantee days on a job."""

from datetime import date, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory
from apps.companies.models import Company
from apps.companies.tests.factories import CompanyFactory
from apps.hiring.models import Placement, PlacementOutcome
from apps.hiring.placements import NOT_DUE
from apps.hiring.tests.factories import PlacementFactory
from apps.jobs.models import GUARANTEE_DAYS_DEFAULT, JobStatus
from apps.ops.tests.helpers import queued_job
from apps.ops.tests.test_jobs_api import detail_url, live_job, put_bounty

pytestmark = pytest.mark.django_db

LIST = "/api/v1/ops/placements"
OPS_FIELDS = {
    "id",
    "application_id",
    "candidate_name",
    "job",
    "recruiter_nickname",
    "start_date",
    "guarantee_days",
    "guarantee_ends_on",
    "days_left",
    "status",
    "outcome",
    "ended_on",
    "outcome_note",
    "can_change_start_date",
    "can_report_no_show",
    "can_report_left",
    "company_name",
    "bounty_type",
    "bounty_pct",
    "bounty_amount",
    "payout_status",
    "paid_amount",
    "paid_at",
    "company",
    "recruiter",
    "payout_note",
}


def today() -> date:
    return timezone.localdate()


def placement(days_from_today: int, **extra: Any) -> Placement:
    job = extra.pop("job", None) or live_job()
    application = cast(Application, ApplicationFactory(job=job, stage=Stage.ONBOARDED))
    return cast(
        Placement,
        PlacementFactory(
            application=application,
            start_date=today() + timedelta(days=days_from_today),
            guarantee_days=60,
            **extra,
        ),
    )


def mark_paid_url(item: Placement) -> str:
    return f"{LIST}/{item.pk}/mark-paid"


def pay(client: APIClient, item: Placement, **payload: Any) -> Any:
    body = {"paid_amount": 12_000_000, "paid_at": str(today()), **payload}
    return client.post(mark_paid_url(item), body, format="json")


def test_ops_filters_by_payout(staff_client: APIClient) -> None:
    not_due = placement(-10)
    due = placement(-70)
    paid = placement(-90, paid_at=today() - timedelta(days=2), paid_amount=5_000_000)
    void = placement(-5, outcome=PlacementOutcome.NO_SHOW)

    def ids(query: str) -> list[str]:
        response = staff_client.get(f"{LIST}{query}")
        assert response.status_code == 200, response.content
        return [row["id"] for row in response.json()["results"]]

    assert ids("") == [str(p.pk) for p in (void, not_due, due, paid)]
    assert staff_client.get(LIST).json()["count"] == 4
    assert ids("?payout_status=not_due") == [str(not_due.pk)]
    assert ids("?payout_status=due") == [str(due.pk)]
    assert ids("?payout_status=paid") == [str(paid.pk)]
    assert ids("?payout_status=void") == [str(void.pk)]
    assert staff_client.get(f"{LIST}?payout_status=bogus").status_code == 400


def test_ops_row_shape(staff_client: APIClient) -> None:
    item = placement(-70)

    row = staff_client.get(LIST).json()["results"][0]

    assert set(row) == OPS_FIELDS
    assert row["company"] == {
        "id": str(item.application.job.company_id),
        "name": item.application.job.company.name,
    }
    assert row["recruiter"] == {
        "id": str(item.application.recruiter_id),
        "nickname": item.application.recruiter.nickname,
    }
    assert row["company_name"] == item.application.job.company.name
    assert (row["status"], row["payout_status"]) == ("passed", "due")


def test_ops_searches_candidate_job_company_and_recruiter(staff_client: APIClient) -> None:
    company = cast(Company, CompanyFactory(name="Zebra Payments"))
    target = placement(-10, job=live_job(company=company, title="Rust Wrangler"))
    target.application.candidate.full_name = "Nguyễn Văn Quyết"
    target.application.candidate.save()
    placement(-10)

    def found(term: str) -> list[str]:
        return [r["id"] for r in staff_client.get(LIST, {"search": term}).json()["results"]]

    for term in ("quyết", "rust wrangler", "zebra", target.application.recruiter.nickname):
        assert str(target.pk) in found(term), term
    assert found("no such thing") == []


def test_ops_placements_are_staff_only(
    client_for: Any, company_admin: User, recruiter_user: User
) -> None:
    item = placement(-70)

    for client in (APIClient(), client_for(company_admin), client_for(recruiter_user)):
        assert client.get(LIST).status_code in (401, 403)
        assert client.post(mark_paid_url(item), {}, format="json").status_code in (401, 403)
    item.refresh_from_db()
    assert item.paid_at is None


def test_mark_paid_only_when_due(staff_client: APIClient, mailoutbox: list[Any]) -> None:
    due = placement(-70)

    response = pay(staff_client, due, note="Bank transfer ref 123")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == OPS_FIELDS
    assert (body["payout_status"], body["paid_amount"], body["paid_at"]) == (
        "paid",
        "12000000",
        str(today()),
    )
    assert body["payout_note"] == "Bank transfer ref 123"
    due.refresh_from_db()
    assert (due.paid_amount, due.paid_at, due.payout_note) == (
        12_000_000,
        today(),
        "Bank transfer ref 123",
    )
    assert due.paid_by is not None
    assert [m.to for m in mailoutbox] == [[due.application.recruiter.user.email]]
    assert "/r/earnings" in mailoutbox[0].body
    assert "12.000.000" in mailoutbox[0].body


@pytest.mark.parametrize("days_from_today", [-10, 5], ids=["in_guarantee", "starting"])
def test_mark_paid_not_yet_due(staff_client: APIClient, days_from_today: int) -> None:
    item = placement(days_from_today)

    response = pay(staff_client, item)

    assert response.status_code == 409
    assert response.json() == {"detail": NOT_DUE}
    item.refresh_from_db()
    assert item.paid_at is None


def test_mark_paid_not_for_a_settled_placement(staff_client: APIClient) -> None:
    item = placement(-70, outcome=PlacementOutcome.LEFT)

    response = pay(staff_client, item)

    assert response.status_code == 409
    assert response.json() == {"detail": NOT_DUE}


def test_mark_paid_twice_conflicts(staff_client: APIClient, mailoutbox: list[Any]) -> None:
    item = placement(-70)
    assert pay(staff_client, item).status_code == 200
    sent = len(mailoutbox)

    again = pay(
        staff_client, item, paid_amount=99_000_000, paid_at=str(today() - timedelta(days=1))
    )

    assert again.status_code == 409
    assert again.json() == {"detail": NOT_DUE}
    item.refresh_from_db()
    assert (item.paid_amount, item.paid_at) == (12_000_000, today())
    assert len(mailoutbox) == sent


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"paid_amount": 0}, "paid_amount"),
        ({"paid_amount": -5}, "paid_amount"),
        ({"paid_amount": "1000.5"}, "paid_amount"),
        ({"paid_amount": 10**15}, "paid_amount"),
        ({"paid_amount": "abc"}, "paid_amount"),
        ({"paid_at": "not a date"}, "paid_at"),
        ({"paid_at": str(timezone.localdate() + timedelta(days=1))}, "paid_at"),
    ],
)
def test_mark_paid_validates_the_form(
    staff_client: APIClient, payload: dict[str, Any], field: str
) -> None:
    item = placement(-70)

    response = pay(staff_client, item, **payload)

    assert response.status_code == 400
    assert field in response.json()
    item.refresh_from_db()
    assert item.paid_at is None


def test_mark_paid_needs_both_fields(staff_client: APIClient) -> None:
    item = placement(-70)

    response = staff_client.post(mark_paid_url(item), {}, format="json")

    assert response.status_code == 400
    assert {"paid_amount", "paid_at"} <= set(response.json())


def test_mark_paid_unknown_placement(staff_client: APIClient) -> None:
    response = staff_client.post(f"{LIST}/{uuid4()}/mark-paid", {}, format="json")

    assert response.status_code == 404


def test_bounty_put_sets_guarantee_days(staff_client: APIClient) -> None:
    job = live_job()
    assert job.guarantee_days == GUARANTEE_DAYS_DEFAULT

    response = put_bounty(
        staff_client, job, {"bounty_type": "pct", "bounty_pct": 20, "guarantee_days": 90}
    )

    assert response.status_code == 200
    assert response.json()["guarantee_days"] == 90
    job.refresh_from_db()
    assert (job.guarantee_days, job.bounty_pct) == (90, 20)
    assert staff_client.get(detail_url(job)).json()["guarantee_days"] == 90


def test_bounty_put_without_guarantee_days_leaves_it_alone(staff_client: APIClient) -> None:
    job = live_job(guarantee_days=45)

    response = put_bounty(staff_client, job, {"bounty_type": "pct", "bounty_pct": 22})

    assert response.status_code == 200
    job.refresh_from_db()
    assert (job.guarantee_days, job.bounty_pct) == (45, 22)


@pytest.mark.parametrize("bad", [0, -1, 366, "abc", 2.5, None])
def test_bounty_put_rejects_a_bad_guarantee(staff_client: APIClient, bad: Any) -> None:
    job = live_job(guarantee_days=45)

    response = put_bounty(
        staff_client, job, {"bounty_type": "pct", "bounty_pct": 22, "guarantee_days": bad}
    )

    assert response.status_code == 400
    assert "guarantee_days" in response.json()
    job.refresh_from_db()
    assert (job.guarantee_days, job.bounty_pct) == (45, 18)


def test_a_running_clock_keeps_its_days_when_the_job_changes(staff_client: APIClient) -> None:
    item = placement(-10)

    put_bounty(
        staff_client,
        item.application.job,
        {"bounty_type": "pct", "bounty_pct": 20, "guarantee_days": 120},
    )

    item.refresh_from_db()
    assert item.guarantee_days == 60


def test_distribute_can_set_the_guarantee_days_too(staff_client: APIClient) -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1, team=None)

    response = staff_client.post(
        f"{detail_url(job)}/distribute",
        {
            "bounty_type": "pct",
            "bounty_pct": 20,
            "guarantee_days": 30,
            "on_marketplace": True,
            "recruiter_ids": [],
        },
        format="json",
    )

    assert response.status_code == 200
    job.refresh_from_db()
    assert (job.status, job.guarantee_days) == (JobStatus.ACTIVE, 30)
