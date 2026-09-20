from django_filters import rest_framework as filters

from apps.jobs.models import Job, JobStatus


class JobFilterSet(filters.FilterSet):
    status = filters.ChoiceFilter(choices=JobStatus.choices)
    function = filters.CharFilter(field_name="function")
    search = filters.CharFilter(field_name="title", lookup_expr="icontains")

    class Meta:
        model = Job
        fields = ("status", "function", "search")
