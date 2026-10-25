"""A hire's life after the offer: start date, guarantee clock, and when the recruiter is owed.

`placement_state` is the one place the dates turn into a status; views and commands ask it.
`today` is always a parameter, so callers decide which calendar day counts (the views pass
`timezone.localdate()`).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from django.db import models, transaction
from django.db.models import DateField, ExpressionWrapper, F, Q, QuerySet
from django.db.models.lookups import GreaterThan, LessThanOrEqual
from django.utils import timezone

from apps.accounts.models import User
from apps.candidates.models import Application
from apps.hiring.emails import notify_recruiter_outcome, notify_recruiter_paid
from apps.hiring.models import Placement, PlacementOutcome

START_DATE_RANGE = "Pick a start date within the last 90 days or the next year."
# Changing the date of someone who has not started: never in the past.
START_DATE_FROM_TODAY = "Pick a start date from today up to a year ahead."
NOT_STARTING = "The start date can only change before the candidate starts."
NO_SHOW_WINDOW = "A no-show can only be reported up to the start date."
LEFT_WINDOW = "A leave can only be reported during the guarantee period."
NOT_DUE = "Only a placement that passed its guarantee and is unpaid can be marked paid."

START_DATE_PAST_DAYS = 90
START_DATE_FUTURE_DAYS = 365
OUTCOME_NOTE_MAX_LENGTH = 1000
# The status filter the company's Hires tabs use for a no-show or a leave together.
ENDED = "ended"


class PlacementStatus(models.TextChoices):
    STARTING = "starting", "Starting soon"
    IN_GUARANTEE = "in_guarantee", "In guarantee"
    PASSED = "passed", "Passed guarantee"
    NO_SHOW = "no_show", "No-show"
    LEFT = "left", "Left during guarantee"


class PayoutStatus(models.TextChoices):
    NOT_DUE = "not_due", "Not due yet"
    DUE = "due", "Due"
    PAID = "paid", "Paid"
    VOID = "void", "Void"


class PlacementConflict(Exception):
    """The command does not fit the placement's dates or state."""


@dataclass(frozen=True)
class PlacementState:
    status: str
    payout_status: str
    guarantee_ends_on: date
    days_left: int


def guarantee_ends_on(placement: Placement) -> date:
    """The first day after the guarantee: on this day the placement has passed."""
    return placement.start_date + timedelta(days=placement.guarantee_days)


def placement_state(placement: Placement, today: date) -> PlacementState:
    ends_on = guarantee_ends_on(placement)
    if placement.outcome:
        # An outcome settles it: no clock, and nothing is owed.
        status = PlacementStatus(placement.outcome)
        return PlacementState(status, PayoutStatus.VOID, ends_on, 0)
    if today < placement.start_date:
        status = PlacementStatus.STARTING
    elif today < ends_on:
        status = PlacementStatus.IN_GUARANTEE
    else:
        status = PlacementStatus.PASSED
    if placement.paid_at is not None:
        payout = PayoutStatus.PAID
    elif status == PlacementStatus.PASSED:
        payout = PayoutStatus.DUE
    else:
        payout = PayoutStatus.NOT_DUE
    return PlacementState(status, payout, ends_on, max(0, (ends_on - today).days))


def check_start_date(start_date: date, today: date) -> None:
    earliest = today - timedelta(days=START_DATE_PAST_DAYS)
    latest = today + timedelta(days=START_DATE_FUTURE_DAYS)
    if not earliest <= start_date <= latest:
        raise PlacementConflict(START_DATE_RANGE)


def create_placement(application: Application, start_date: date, today: date) -> Placement:
    check_start_date(start_date, today)
    return Placement.objects.create(
        application=application,
        start_date=start_date,
        guarantee_days=application.job.guarantee_days,
    )


def _locked(pk: UUID) -> Placement:
    return (
        Placement.objects.select_for_update(of=("self",))
        .select_related(
            "application__job__company", "application__candidate", "application__recruiter__user"
        )
        .get(pk=pk)
    )


def change_start_date(placement: Placement, start_date: date, today: date) -> Placement:
    with transaction.atomic():
        current = _locked(placement.pk)
        if placement_state(current, today).status != PlacementStatus.STARTING:
            raise PlacementConflict(NOT_STARTING)
        # Never the past, unlike the day it was first set: the candidate has not started yet.
        if not today <= start_date <= today + timedelta(days=START_DATE_FUTURE_DAYS):
            raise PlacementConflict(START_DATE_FROM_TODAY)
        current.start_date = start_date
        current.save(update_fields=["start_date"])
    return current


def report_outcome(
    placement: Placement,
    user: User,
    outcome: str,
    ended_on: date | None,
    note: str,
    today: date,
) -> Placement:
    """The candidate never came (`no_show`) or left during the guarantee (`left`)."""
    if outcome not in PlacementOutcome.values:
        raise ValueError(f"outcome must be one of {PlacementOutcome.values}")
    with transaction.atomic():
        current = _locked(placement.pk)
        if outcome == PlacementOutcome.NO_SHOW:
            if current.outcome or today > current.start_date:
                raise PlacementConflict(NO_SHOW_WINDOW)
            ended_on = current.start_date
        else:
            status = placement_state(current, today).status
            if status != PlacementStatus.IN_GUARANTEE:
                raise PlacementConflict(LEFT_WINDOW)
            if ended_on is None or not current.start_date <= ended_on <= today:
                raise PlacementConflict(LEFT_WINDOW)
            if not note or len(note) > OUTCOME_NOTE_MAX_LENGTH:
                raise ValueError("a leave needs a note of up to 1000 characters")
        current.outcome = outcome
        current.ended_on = ended_on
        current.outcome_note = note
        current.reported_by = user
        current.reported_at = timezone.now()
        current.save(
            update_fields=["outcome", "ended_on", "outcome_note", "reported_by", "reported_at"]
        )
    notify_recruiter_outcome(current)
    return current


def mark_paid(
    placement: Placement, staff: User, amount: Decimal, paid_at: date, note: str, today: date
) -> Placement:
    """Sparktal paid the recruiter. Only a placement that passed its guarantee can be."""
    if amount <= 0:
        raise ValueError("amount must be more than zero")
    if paid_at > today:
        raise ValueError("paid_at cannot be in the future")
    with transaction.atomic():
        current = _locked(placement.pk)
        if placement_state(current, today).payout_status != PayoutStatus.DUE:
            raise PlacementConflict(NOT_DUE)
        current.paid_amount = amount
        current.paid_at = paid_at
        current.payout_note = note
        current.paid_by = staff
        current.save(update_fields=["paid_amount", "paid_at", "payout_note", "paid_by"])
    notify_recruiter_paid(current)
    return current


def with_ends_on(placements: QuerySet[Placement]) -> QuerySet[Placement]:
    """Annotate `ends_on`: `guarantee_ends_on` in SQL, the first day after the guarantee."""
    if "ends_on" in placements.query.annotations:
        return placements
    ends_on = ExpressionWrapper(
        F("start_date") + F("guarantee_days") * timedelta(days=1), output_field=DateField()
    )
    return placements.annotate(ends_on=ends_on)


def _ends_after(today: date) -> GreaterThan:
    return GreaterThan(F("ends_on"), today)


def _ended_by(today: date) -> LessThanOrEqual:
    return LessThanOrEqual(F("ends_on"), today)


def filter_by_status(
    placements: QuerySet[Placement], status: str, today: date
) -> QuerySet[Placement]:
    """The placements `placement_state` gives `status`; `ended` is a no-show or a leave."""
    placements = with_ends_on(placements)
    open_ = Q(outcome="")
    if status == ENDED:
        return placements.exclude(outcome="")
    if status == PlacementStatus.NO_SHOW or status == PlacementStatus.LEFT:
        return placements.filter(outcome=status)
    if status == PlacementStatus.STARTING:
        return placements.filter(open_, start_date__gt=today)
    if status == PlacementStatus.IN_GUARANTEE:
        return placements.filter(open_, _ends_after(today), start_date__lte=today)
    if status == PlacementStatus.PASSED:
        return placements.filter(open_, _ended_by(today))
    raise ValueError(f"unknown placement status {status!r}")


def filter_by_payout(
    placements: QuerySet[Placement], payout: str, today: date
) -> QuerySet[Placement]:
    """The placements `placement_state` gives the payout status `payout`."""
    placements = with_ends_on(placements)
    open_ = Q(outcome="")
    if payout == PayoutStatus.VOID:
        return placements.exclude(outcome="")
    if payout == PayoutStatus.PAID:
        return placements.filter(open_, paid_at__isnull=False)
    if payout == PayoutStatus.DUE:
        return placements.filter(open_, _ended_by(today), paid_at__isnull=True)
    if payout == PayoutStatus.NOT_DUE:
        return placements.filter(open_, _ends_after(today), paid_at__isnull=True)
    raise ValueError(f"unknown payout status {payout!r}")
