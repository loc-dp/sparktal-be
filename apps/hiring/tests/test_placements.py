from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from apps.accounts.models import UserKind
from apps.accounts.tests.factories import UserFactory
from apps.candidates.models import Stage
from apps.hiring.models import Placement, PlacementOutcome
from apps.hiring.placements import (
    START_DATE_RANGE,
    PayoutStatus,
    PlacementConflict,
    PlacementStatus,
    create_placement,
    filter_by_payout,
    filter_by_status,
    mark_paid,
    placement_state,
)
from apps.hiring.tests.conftest import at_stage
from apps.hiring.tests.factories import PlacementFactory
from apps.jobs.models import GUARANTEE_DAYS_DEFAULT

pytestmark = pytest.mark.django_db

START = date(2026, 10, 1)


def placement(**overrides: Any) -> Placement:
    return Placement(start_date=START, guarantee_days=60, **overrides)


@pytest.mark.parametrize(
    ("today", "status", "payout", "days_left"),
    [
        (date(2026, 9, 30), PlacementStatus.STARTING, PayoutStatus.NOT_DUE, 61),
        (date(2026, 10, 1), PlacementStatus.IN_GUARANTEE, PayoutStatus.NOT_DUE, 60),
        (date(2026, 11, 29), PlacementStatus.IN_GUARANTEE, PayoutStatus.NOT_DUE, 1),
        (date(2026, 11, 30), PlacementStatus.PASSED, PayoutStatus.DUE, 0),
        (date(2027, 3, 1), PlacementStatus.PASSED, PayoutStatus.DUE, 0),
    ],
)
def test_state_boundaries(
    today: date, status: PlacementStatus, payout: PayoutStatus, days_left: int
) -> None:
    state = placement_state(placement(), today)
    assert (state.status, state.payout_status, state.days_left) == (status, payout, days_left)
    assert state.guarantee_ends_on == date(2026, 11, 30)


def test_a_paid_placement_is_paid() -> None:
    state = placement_state(placement(paid_at=date(2026, 12, 5)), date(2026, 12, 6))
    assert (state.status, state.payout_status) == (PlacementStatus.PASSED, PayoutStatus.PAID)


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        (PlacementOutcome.LEFT, PlacementStatus.LEFT),
        (PlacementOutcome.NO_SHOW, PlacementStatus.NO_SHOW),
    ],
)
def test_an_outcome_settles_the_placement(
    outcome: PlacementOutcome, status: PlacementStatus
) -> None:
    state = placement_state(placement(outcome=outcome), date(2026, 10, 20))
    assert (state.status, state.payout_status, state.days_left) == (status, PayoutStatus.VOID, 0)


def test_create_placement_copies_the_jobs_guarantee(application: Any) -> None:
    application.job.guarantee_days = 90
    application.job.save()
    today = date(2026, 10, 25)
    created = create_placement(application, today, today)
    assert (created.start_date, created.guarantee_days) == (today, 90)


@pytest.mark.parametrize("offset", [-91, 366])
def test_create_placement_rejects_dates_out_of_range(application: Any, offset: int) -> None:
    today = date(2026, 10, 25)
    with pytest.raises(PlacementConflict, match=START_DATE_RANGE):
        create_placement(application, today + timedelta(days=offset), today)
    assert not Placement.objects.exists()


@pytest.mark.parametrize("offset", [-90, 365])
def test_create_placement_accepts_the_range_edges(application: Any, offset: int) -> None:
    today = date(2026, 10, 25)
    assert create_placement(application, today + timedelta(days=offset), today).pk


def post_hire(client: Any, application: Any, body: dict[str, Any] | None = None) -> Any:
    return client.post(f"/api/v1/applications/{application.pk}/hire", body or {}, format="json")


def test_hire_requires_start_date(ta_client: Any, application: Any) -> None:
    at_stage(application, Stage.OFFER)
    response = post_hire(ta_client, application)
    assert response.status_code == 400
    assert "start_date" in response.json()
    application.refresh_from_db()
    assert application.stage == Stage.OFFER


def test_hire_with_a_start_date_creates_the_placement(ta_client: Any, application: Any) -> None:
    at_stage(application, Stage.OFFER)
    start = timezone.localdate() + timedelta(days=14)
    response = post_hire(ta_client, application, {"start_date": start.isoformat()})
    assert response.status_code == 200
    assert response.json()["stage"] == "onboarded"
    created = Placement.objects.get(application=application)
    assert created.start_date == start
    assert created.guarantee_days == application.job.guarantee_days == GUARANTEE_DAYS_DEFAULT


def test_hire_start_date_out_of_range(ta_client: Any, application: Any) -> None:
    at_stage(application, Stage.OFFER)
    start = timezone.localdate() - timedelta(days=91)
    response = post_hire(ta_client, application, {"start_date": start.isoformat()})
    assert response.status_code == 400
    assert response.json() == {"start_date": [START_DATE_RANGE]}
    application.refresh_from_db()
    assert application.stage == Stage.OFFER
    assert not Placement.objects.exists()


def test_hire_twice_is_a_conflict_and_keeps_one_placement(ta_client: Any, application: Any) -> None:
    at_stage(application, Stage.OFFER)
    body = {"start_date": timezone.localdate().isoformat()}
    assert post_hire(ta_client, application, body).status_code == 200
    assert post_hire(ta_client, application, body).status_code == 409
    assert Placement.objects.filter(application=application).count() == 1


def test_factory_placement_follows_the_job_guarantee() -> None:
    created = PlacementFactory()
    assert created.guarantee_days == GUARANTEE_DAYS_DEFAULT


def spread_of_placements(today: date) -> list[Placement]:
    """One placement at every edge of the clock, settled ones and paid ones included."""
    made: list[Placement] = []
    for days_ago in (-30, -1, 0, 1, 59, 60, 61, 200):
        made.append(
            PlacementFactory(start_date=today - timedelta(days=days_ago), guarantee_days=60)
        )
        made.append(
            PlacementFactory(
                start_date=today - timedelta(days=days_ago),
                guarantee_days=60,
                paid_at=today,
                paid_amount=1_000_000,
            )
        )
        made.append(
            PlacementFactory(
                start_date=today - timedelta(days=days_ago),
                guarantee_days=60,
                outcome=PlacementOutcome.NO_SHOW,
            )
        )
        made.append(
            PlacementFactory(
                start_date=today - timedelta(days=days_ago),
                guarantee_days=60,
                outcome=PlacementOutcome.LEFT,
            )
        )
    made.append(PlacementFactory(start_date=today, guarantee_days=1))
    made.append(PlacementFactory(start_date=today - timedelta(days=1), guarantee_days=1))
    made.append(PlacementFactory(start_date=today, guarantee_days=365))
    return made


@pytest.mark.parametrize("status", [*PlacementStatus.values, "ended"])
def test_filter_by_status_agrees_with_placement_state(status: str) -> None:
    today = timezone.localdate()
    made = spread_of_placements(today)
    wanted = {PlacementStatus.NO_SHOW, PlacementStatus.LEFT} if status == "ended" else {status}

    found = set(filter_by_status(Placement.objects.all(), status, today))

    assert found == {p for p in made if placement_state(p, today).status in wanted}
    assert found  # the spread covers every status


@pytest.mark.parametrize("payout", PayoutStatus.values)
def test_filter_by_payout_agrees_with_placement_state(payout: str) -> None:
    today = timezone.localdate()
    made = spread_of_placements(today)

    found = set(filter_by_payout(Placement.objects.all(), payout, today))

    assert found == {p for p in made if placement_state(p, today).payout_status == payout}
    assert found


def test_the_two_filters_stack() -> None:
    today = timezone.localdate()
    made = spread_of_placements(today)

    found = set(
        filter_by_payout(
            filter_by_status(Placement.objects.all(), PlacementStatus.PASSED, today),
            PayoutStatus.PAID,
            today,
        )
    )

    assert found == {
        p
        for p in made
        if (state := placement_state(p, today)).status == PlacementStatus.PASSED
        and state.payout_status == PayoutStatus.PAID
    }
    assert found


def test_mark_paid_guards_amount_and_date() -> None:
    staff_user = UserFactory(kind=UserKind.STAFF, is_staff=True)
    today = timezone.localdate()
    due = PlacementFactory(start_date=today - timedelta(days=90), guarantee_days=60)

    with pytest.raises(ValueError, match="amount"):
        mark_paid(due, staff_user, Decimal(0), today, "", today)
    with pytest.raises(ValueError, match="paid_at"):
        mark_paid(due, staff_user, Decimal(1), today + timedelta(days=1), "", today)
    due.refresh_from_db()
    assert due.paid_at is None
