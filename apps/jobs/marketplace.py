from typing import TYPE_CHECKING

from django.conf import settings
from django.db.models import Count, Exists, F, Func, Max, OuterRef, Q, QuerySet, TextField, Value
from django.db.models.functions import Coalesce, Concat
from django_filters import rest_framework as filters

from apps.candidates.models import Stage
from apps.companies.models import Location
from apps.jobs.access import live_jobs
from apps.jobs.models import Job, JobAssignment, JobFunction, WorkModel

if TYPE_CHECKING:
    from apps.recruiters.models import RecruiterProfile


class ArrayToString(Func):
    function = "array_to_string"
    output_field = TextField()


def marketplace_queryset(recruiter: "RecruiterProfile | None") -> QuerySet[Job]:
    """The list and what Join takes: live jobs Sparktal put on the marketplace; nothing while
    the marketplace is switched off, the lock under the view gate."""
    if recruiter is None or not settings.MARKETPLACE_ENABLED:
        return Job.objects.none()
    return (
        live_jobs()
        .filter(on_marketplace=True)
        .select_related("company", "office")
        .order_by("-published_at", "-created_at")
    )


def annotate_marketplace(
    queryset: QuerySet[Job], recruiter: "RecruiterProfile | None"
) -> QuerySet[Job]:
    mine = JobAssignment.objects.filter(
        job=OuterRef("pk"), recruiter=recruiter, left_at__isnull=True
    )
    return queryset.annotate(
        joined=Exists(mine),
        submitted_count=Count(
            "applications", filter=Q(applications__recruiter=recruiter), distinct=True
        ),
        recruiters_working=Count(
            "assignments", filter=Q(assignments__left_at__isnull=True), distinct=True
        ),
    )


def annotate_joined_jobs(
    queryset: QuerySet[Job], recruiter: "RecruiterProfile | None"
) -> QuerySet[Job]:
    mine = Q(applications__recruiter=recruiter)
    held = JobAssignment.objects.filter(
        job=OuterRef("pk"), recruiter=recruiter, left_at__isnull=True
    )
    return (
        queryset.annotate(
            assigned=Exists(held),
            submitted_count=Count("applications", filter=mine, distinct=True),
            # Still in play: neither rejected nor hired.
            active_count=Count(
                "applications",
                filter=mine & ~Q(applications__stage__in=(Stage.REJECTED, Stage.ONBOARDED)),
                distinct=True,
            ),
            hired_count=Count(
                "applications", filter=mine & Q(applications__stage=Stage.ONBOARDED), distinct=True
            ),
            joined_at=Max(
                "assignments__joined_at",
                filter=Q(assignments__recruiter=recruiter, assignments__left_at__isnull=True),
            ),
            last_stage_change=Max("applications__stage_changed_at", filter=mine),
        )
        .annotate(last_activity_at=Coalesce("last_stage_change", "joined_at"))
        # Jobs they still hold first, then the removed ones they keep for their CVs.
        .order_by("-assigned", F("last_activity_at").desc(nulls_last=True), "-joined_at")
    )


class MarketplaceFilterSet(filters.FilterSet):  # type: ignore[misc]
    search = filters.CharFilter(method="filter_search")
    function = filters.ChoiceFilter(choices=JobFunction.choices)
    work_model = filters.ChoiceFilter(choices=WorkModel.choices)
    location = filters.ChoiceFilter(field_name="office__city", choices=Location.choices)
    joined = filters.BooleanFilter(field_name="joined")

    class Meta:
        model = Job
        fields = ("search", "function", "work_model", "location", "joined")

    def filter_search(self, queryset: QuerySet[Job], name: str, value: str) -> QuerySet[Job]:
        term = (value or "").strip()
        if not term:
            return queryset
        return queryset.annotate(
            haystack=Concat(
                "title",
                Value(" "),
                "company__name",
                Value(" "),
                ArrayToString("skills", Value(" ")),
                output_field=TextField(),
            )
        ).filter(haystack__icontains=term)
