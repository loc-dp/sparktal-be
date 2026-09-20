"""Pipeline stage order shared by the API, the admin and the metrics annotations."""

from collections.abc import Iterable

from django.db.models import Case, IntegerField, Value, When

STAGE_ORDER: list[str] = ["shortlisted", "new", "screen", "interview", "offer", "onboarded"]


def current_stage(stages: Iterable[str]) -> str | None:
    """The most advanced stage in `stages`; `rejected` never counts."""
    ranked = [STAGE_ORDER.index(stage) for stage in stages if stage in STAGE_ORDER]
    return STAGE_ORDER[max(ranked)] if ranked else None


def stage_rank_case(field: str = "applications__stage") -> Case:
    """SQL counterpart of `current_stage`: ranks a stage column, -1 for rejected."""
    return Case(
        *[When(**{field: stage}, then=Value(index)) for index, stage in enumerate(STAGE_ORDER)],
        default=Value(-1),
        output_field=IntegerField(),
    )
