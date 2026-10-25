from typing import Any

from django.db.models import F, Q, QuerySet
from django.http import QueryDict
from django.utils import timezone
from django_filters import rest_framework as filters

from apps.companies.models import Company
from apps.hiring.models import Placement
from apps.hiring.placements import PayoutStatus, filter_by_payout
from apps.jobs.models import Job, JobStatus
from apps.recruiters.models import RecruiterProfile, RecruiterStatus

# The tabs of the queue. A draft or a job still with the company is not Sparktal's yet.
OPS_STATUSES = (JobStatus.PENDING, JobStatus.ACTIVE, JobStatus.CLOSED)


class OpsJobFilterSet(filters.FilterSet):
    """`?status=` (default `pending`), `?search=` on title or company, `?company=`."""

    status = filters.ChoiceFilter(
        choices=[(status.value, status.label) for status in OPS_STATUSES],
        empty_label=None,
        help_text="Which tab: the queue (`pending`, the default), or `active` or `closed`.",
    )
    search = filters.CharFilter(method="filter_search", help_text="Part of the title or company.")
    company = filters.UUIDFilter(field_name="company_id", help_text="Only this company's jobs.")

    class Meta:
        model = Job
        fields = ("status", "search", "company")

    def __init__(self, data: Any = None, *args: Any, **kwargs: Any) -> None:
        data = data.copy() if data is not None else QueryDict(mutable=True)
        if not data.get("status"):
            data["status"] = JobStatus.PENDING
        super().__init__(data, *args, **kwargs)

    def filter_search(self, queryset: QuerySet[Job], name: str, value: str) -> QuerySet[Job]:
        term = (value or "").strip()
        if not term:
            return queryset
        return queryset.filter(Q(title__icontains=term) | Q(company__name__icontains=term))

    def filter_queryset(self, queryset: QuerySet[Job]) -> QuerySet[Job]:
        """The queue is oldest first; the other tabs show what went live last first."""
        queryset = super().filter_queryset(queryset)
        if self.form.cleaned_data["status"] == JobStatus.PENDING:
            return queryset.order_by(F("posted_at").asc(nulls_last=True), "id")
        return queryset.order_by(F("published_at").desc(nulls_last=True), "id")


class OpsCompanyFilterSet(filters.FilterSet):
    """`?search=` on the company name."""

    search = filters.CharFilter(method="filter_search", help_text="Part of the company name.")

    class Meta:
        model = Company
        fields = ("search",)

    def filter_search(
        self, queryset: QuerySet[Company], name: str, value: str
    ) -> QuerySet[Company]:
        term = (value or "").strip()
        return queryset.filter(name__icontains=term) if term else queryset


class OpsRecruiterFilterSet(filters.FilterSet):
    """`?status=` (any of the three, none for all), `?search=` on nickname, name or email."""

    status = filters.ChoiceFilter(
        choices=RecruiterStatus.choices,
        help_text="Only recruiters in this state. Leave it out for all of them.",
    )
    search = filters.CharFilter(
        method="filter_search", help_text="Part of the nickname, full name or email."
    )

    class Meta:
        model = RecruiterProfile
        fields = ("status", "search")

    def filter_search(
        self, queryset: QuerySet[RecruiterProfile], name: str, value: str
    ) -> QuerySet[RecruiterProfile]:
        term = (value or "").strip()
        if not term:
            return queryset
        return queryset.filter(
            Q(nickname__icontains=term)
            | Q(user__full_name__icontains=term)
            | Q(user__email__icontains=term)
        )

    def filter_queryset(self, queryset: QuerySet[RecruiterProfile]) -> QuerySet[RecruiterProfile]:
        """The review queue is oldest first; every other list reads by nickname (the invite
        picker lists `approved` that way)."""
        queryset = super().filter_queryset(queryset)
        if self.form.cleaned_data["status"] == RecruiterStatus.PENDING:
            return queryset.order_by(F("submitted_at").asc(nulls_last=True), "nickname", "id")
        return queryset.order_by("nickname", "id")


class OpsPlacementFilterSet(filters.FilterSet):
    """`?payout_status=` and `?search=` on the candidate, the job, the company or the recruiter."""

    payout_status = filters.ChoiceFilter(
        choices=PayoutStatus.choices,
        method="filter_payout_status",
        help_text="Where the payout stands; `due` is what Sparktal still has to pay.",
    )
    search = filters.CharFilter(
        method="filter_search",
        help_text="Part of the candidate's name, the job title, the company or the recruiter.",
    )

    class Meta:
        model = Placement
        fields = ("payout_status", "search")

    def filter_payout_status(
        self, queryset: QuerySet[Placement], name: str, value: str
    ) -> QuerySet[Placement]:
        return filter_by_payout(queryset, value, timezone.localdate()) if value else queryset

    def filter_search(
        self, queryset: QuerySet[Placement], name: str, value: str
    ) -> QuerySet[Placement]:
        term = (value or "").strip()
        if not term:
            return queryset
        return queryset.filter(
            Q(application__candidate__full_name__icontains=term)
            | Q(application__job__title__icontains=term)
            | Q(application__job__company__name__icontains=term)
            | Q(application__recruiter__nickname__icontains=term)
        )
