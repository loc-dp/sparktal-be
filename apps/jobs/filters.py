from typing import Any

from django_filters import rest_framework as filters

from apps.jobs.models import Job, JobStatus


class StatusInFilter(filters.BaseInFilter, filters.ChoiceFilter):
    """`?status=draft,submitted`; a status that does not exist is a 400."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, choices=JobStatus.choices, **kwargs)


class JobFilterSet(filters.FilterSet):
    status = StatusInFilter(field_name="status", lookup_expr="in")
    function = filters.CharFilter(field_name="function")
    search = filters.CharFilter(field_name="title", lookup_expr="icontains")

    class Meta:
        model = Job
        fields = ("status", "function", "search")
