"""Derived numbers for the My jobs table, computed in SQL."""

from typing import Any

from django.db.models import (
    Case,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    IntegerField,
    Max,
    Min,
    Q,
    QuerySet,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Extract, Now

from apps.candidates.stages import stage_rank_case
from apps.jobs.models import Job


def _days(expression: Any) -> Any:
    """Whole days out of a duration; Postgres gives the epoch in seconds."""
    return ExpressionWrapper(
        Extract(ExpressionWrapper(expression, output_field=DurationField()), "epoch") / 86400,
        output_field=IntegerField(),
    )


def annotate_job_metrics(queryset: QuerySet[Job]) -> QuerySet[Job]:
    opened_at = Coalesce("published_at", "created_at")
    closed_or_now = Coalesce("closed_at", Now())

    return queryset.annotate(
        cv_count=Count("applications", distinct=True),
        new_count=Count("applications", filter=Q(applications__stage="new"), distinct=True),
        stage_rank=Max(stage_rank_case()),
        days_open=_days(closed_or_now - opened_at),
        first_onboarded_at=Min(
            Case(
                When(applications__stage="onboarded", then=F("applications__stage_changed_at")),
                default=Value(None),
            )
        ),
    ).annotate(days_to_onboard=_days(F("first_onboarded_at") - opened_at))
