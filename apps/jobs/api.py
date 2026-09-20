from django.db.models import Count, Q, QuerySet
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.generics import ListCreateAPIView, RetrieveAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.candidates.models import Stage
from apps.jobs.access import can_change_job, jobs_visible_to
from apps.jobs.filters import JobFilterSet
from apps.jobs.models import Job, JobStatus
from apps.jobs.queries import annotate_job_metrics
from apps.jobs.serializers import (
    JobCountsSerializer,
    JobListItemSerializer,
    JobOverviewSerializer,
    JobSerializer,
    PublicJobSerializer,
)


def _user(view: APIView) -> User | None:
    """`request.user` is `User | AnonymousUser`; the access helpers want `User | None`."""
    user = view.request.user
    return user if isinstance(user, User) else None


class CanAddJob(BasePermission):
    """Checked before validation so a recruiter gets 403, not a field error."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        return bool(request.user.has_perm("jobs.add_job"))


@extend_schema_view(
    get=extend_schema(operation_id="jobs_list", tags=["jobs"]),
    post=extend_schema(operation_id="jobs_create", tags=["jobs"]),
)
class JobListCreateView(ListCreateAPIView[Job]):
    permission_classes = (CanAddJob,)
    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_class = JobFilterSet
    ordering_fields = (
        "title",
        "created_at",
        "function",
        "stage_rank",
        "cv_count",
        "new_count",
        "days_open",
        "days_to_onboard",
    )
    ordering = ("-created_at",)

    def get_serializer_class(self) -> type[JobSerializer] | type[JobListItemSerializer]:
        return JobListItemSerializer if self.request.method == "GET" else JobSerializer

    def get_queryset(self) -> QuerySet[Job]:
        return annotate_job_metrics(
            jobs_visible_to(_user(self)).select_related("company", "office")
        )

    def get_ordering(self) -> list[str]:
        """`current_stage` is exposed to the client but ranked in SQL."""
        requested = self.request.query_params.get("ordering")
        if requested:
            return [requested.replace("current_stage", "stage_rank")]
        return list(self.ordering)


@extend_schema_view(
    get=extend_schema(operation_id="jobs_retrieve", tags=["jobs"]),
    patch=extend_schema(operation_id="jobs_partial_update", tags=["jobs"]),
)
class JobDetailView(RetrieveUpdateAPIView[Job]):
    serializer_class = JobSerializer
    http_method_names = ("get", "patch", "head", "options")

    def get_queryset(self) -> QuerySet[Job]:
        return jobs_visible_to(_user(self)).select_related("company", "office")

    def get_object(self) -> Job:
        job = super().get_object()
        # 404, not 403: a job you may not edit should not be distinguishable.
        if self.request.method == "PATCH" and not can_change_job(_user(self), job):
            raise Http404
        return job


@extend_schema_view(get=extend_schema(operation_id="public_jobs_retrieve", tags=["public"]))
class PublicJobView(RetrieveAPIView[Job]):
    serializer_class = PublicJobSerializer
    permission_classes = (AllowAny,)
    authentication_classes = ()
    lookup_field = "share_token"

    def get_queryset(self) -> QuerySet[Job]:
        return Job.objects.filter(status=JobStatus.ACTIVE).select_related("company")


@extend_schema_view(
    get=extend_schema(
        operation_id="jobs_counts_retrieve", tags=["jobs"], responses=JobCountsSerializer
    )
)
class JobCountsView(APIView):
    def get(self, request: Request) -> Response:
        rows = jobs_visible_to(_user(self)).values("status").annotate(total=Count("id"))
        counts = {status: 0 for status in ("pending", "active", "closed")}
        for row in rows:
            counts[row["status"]] = row["total"]
        return Response(counts)


class JobActionView(APIView):
    """Shared lookup: 404 (never 403) when the user may not touch this job."""

    write = False

    def get_job(self, pk: str) -> Job:
        job = get_object_or_404(jobs_visible_to(_user(self)), pk=pk)
        if self.write and not can_change_job(_user(self), job):
            raise Http404
        return job


@extend_schema_view(
    post=extend_schema(
        operation_id="jobs_close_create", tags=["jobs"], request=None, responses=JobSerializer
    )
)
class JobCloseView(JobActionView):
    write = True

    def post(self, request: Request, pk: str) -> Response:
        job = self.get_job(pk)
        if job.status != JobStatus.ACTIVE:
            raise ValidationError({"status": ["Only an active job can be closed."]})
        job.close()
        return Response(JobSerializer(job, context={"request": request}).data)


@extend_schema_view(
    post=extend_schema(
        operation_id="jobs_reopen_create", tags=["jobs"], request=None, responses=JobSerializer
    )
)
class JobReopenView(JobActionView):
    write = True

    def post(self, request: Request, pk: str) -> Response:
        job = self.get_job(pk)
        if job.status != JobStatus.CLOSED:
            raise ValidationError({"status": ["Only a closed job can be reopened."]})
        job.reopen()
        return Response(JobSerializer(job, context={"request": request}).data)


@extend_schema_view(
    get=extend_schema(
        operation_id="jobs_overview_retrieve", tags=["jobs"], responses=JobOverviewSerializer
    )
)
class JobOverviewView(JobActionView):
    def get(self, request: Request, pk: str) -> Response:
        job = self.get_job(pk)
        applications = job.applications.select_related("candidate", "recruiter")

        counts = applications.aggregate(
            applicants=Count("id"),
            screen=Count("id", filter=Q(stage=Stage.SCREEN)),
            interviewing=Count("id", filter=Q(stage=Stage.INTERVIEW)),
            offer=Count("id", filter=Q(stage=Stage.OFFER)),
        )
        opened_at = job.published_at or job.created_at
        end = job.closed_at or timezone.now()

        payload: dict[str, object] = {
            "kpis": {
                "days_open": max((end - opened_at).days, 0),
                "applicants": counts["applicants"],
                "screen": counts["screen"],
                "interviewing": counts["interviewing"],
                "offer": counts["offer"],
            },
            "new_applicants": [
                {
                    "id": application.id,
                    "candidate_name": application.candidate.full_name,
                    "current_title": application.candidate.current_title,
                    "recruiter_nickname": application.recruiter.nickname,
                    "submitted_at": application.submitted_at,
                    "is_duplicate": application.is_duplicate,
                }
                for application in applications.filter(stage=Stage.NEW).order_by("-submitted_at")[
                    :5
                ]
            ],
            "rejection_log": [
                {
                    "candidate_name": application.candidate.full_name,
                    "reason": application.rejected_reason,
                    "rejected_at": application.stage_changed_at,
                }
                for application in applications.filter(stage=Stage.REJECTED).order_by(
                    "-stage_changed_at"
                )[:20]
            ],
            "recruiters": [
                {
                    "id": row["recruiter__id"],
                    "nickname": row["recruiter__nickname"],
                    "cv_count": row["total"],
                }
                for row in applications.values("recruiter__id", "recruiter__nickname")
                .annotate(total=Count("id"))
                .order_by("-total")
            ],
            "note_for_recruiters": job.note_for_recruiters,
        }
        return Response(JobOverviewSerializer(payload).data)
