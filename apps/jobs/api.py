from collections.abc import Callable
from typing import Any

from django.db import transaction
from django.db.models import Count, OuterRef, Q, QuerySet, Subquery
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import APIException, ParseError, PermissionDenied
from rest_framework.filters import OrderingFilter
from rest_framework.generics import (
    ListAPIView,
    ListCreateAPIView,
    RetrieveAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.candidates.models import Stage
from apps.companies.access import company_of
from apps.jobs.access import (
    NOT_YOURS,
    can_change_job,
    jobs_joined_by,
    jobs_offered_to,
    jobs_visible_to,
)
from apps.jobs.distribution import join_marketplace_job
from apps.jobs.filters import JobFilterSet
from apps.jobs.jd_parsing import is_enabled as jd_parsing_enabled
from apps.jobs.lifecycle import (
    close_job,
    delete_job,
    post_job,
    reopen_job,
    submit_job,
    withdraw_job,
)
from apps.jobs.marketplace import MarketplaceFilterSet, annotate_marketplace, marketplace_queryset
from apps.jobs.models import ChangeStatus, JdParse, Job, JobAssignment, JobChangeRequest, JobStatus
from apps.jobs.proposals import (
    approve_change,
    change_requests_visible_to,
    is_admin,
    propose_change,
    reject_change,
    withdraw_change,
)
from apps.jobs.queries import annotate_job_metrics
from apps.jobs.serializers import (
    ChangeRequestCreateSerializer,
    ChangeRequestDecisionSerializer,
    ChangeRequestSerializer,
    JdParseAvailabilitySerializer,
    JdParseCreateSerializer,
    JdParseSerializer,
    JobCountsSerializer,
    JobListItemSerializer,
    JobOverviewSerializer,
    JobSerializer,
    JoinResultSerializer,
    LeaveResultSerializer,
    MarketplaceJobListItemSerializer,
    MarketplaceJobSerializer,
    PublicJobSerializer,
)
from apps.jobs.tasks import parse_jd
from apps.recruiters.access import IsApprovedRecruiter, approved_recruiter
from apps.recruiters.models import RecruiterProfile


def _user(view: APIView) -> User | None:
    """`request.user` is `User | AnonymousUser`; the access helpers want `User | None`."""
    user = view.request.user
    return user if isinstance(user, User) else None


def _actor(view: APIView) -> User:
    user = _user(view)
    assert user is not None, "authentication runs before the view"
    return user


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
        user = _user(self)
        pending = JobChangeRequest.objects.filter(job=OuterRef("pk"), status=ChangeStatus.PENDING)
        if not is_admin(user):
            pending = pending.filter(requested_by=user)
        counted = pending.order_by().values("job").annotate(total=Count("pk")).values("total")
        return annotate_job_metrics(
            jobs_visible_to(user).select_related("company", "office", "team")
        ).annotate(pending_changes=Coalesce(Subquery(counted), 0))

    def get_ordering(self) -> list[str]:
        """`current_stage` is exposed to the client but ranked in SQL."""
        requested = self.request.query_params.get("ordering")
        if requested:
            return [requested.replace("current_stage", "stage_rank")]
        return list(self.ordering)


@extend_schema_view(
    get=extend_schema(operation_id="jobs_retrieve", tags=["jobs"]),
    patch=extend_schema(operation_id="jobs_partial_update", tags=["jobs"]),
    delete=extend_schema(operation_id="jobs_destroy", tags=["jobs"]),
)
class JobDetailView(RetrieveUpdateDestroyAPIView[Job]):
    serializer_class = JobSerializer
    http_method_names = ("get", "patch", "delete", "head", "options")

    def get_queryset(self) -> QuerySet[Job]:
        return jobs_visible_to(_user(self)).select_related("company", "office")

    def get_object(self) -> Job:
        job = super().get_object()
        if self.request.method == "PATCH" and not can_change_job(_user(self), job):
            raise PermissionDenied(NOT_YOURS)
        return job

    def perform_destroy(self, instance: Job) -> None:
        delete_job(instance, actor=_actor(self))


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
        counts = {str(status): 0 for status in JobStatus}
        for row in rows:
            counts[row["status"]] = row["total"]
        return Response(counts)


class JobActionView(APIView):
    """Shared lookup: 404 when the user may not see this job."""

    def get_job(self, pk: str) -> Job:
        return get_object_or_404(jobs_visible_to(_user(self)), pk=pk)


class JobTransitionView(JobActionView):
    """One lifecycle function, which checks the matching permission itself (403 if not)."""

    transition: Callable[..., Job]

    def post(self, request: Request, pk: str) -> Response:
        job = self.transition(self.get_job(pk), actor=_actor(self))
        return Response(JobSerializer(job, context={"request": request}).data)


def _transition_schema(operation_id: str) -> Any:
    return extend_schema_view(
        post=extend_schema(
            operation_id=operation_id, tags=["jobs"], request=None, responses=JobSerializer
        )
    )


@_transition_schema("jobs_submit_create")
class JobSubmitView(JobTransitionView):
    transition = staticmethod(submit_job)


@_transition_schema("jobs_withdraw_create")
class JobWithdrawView(JobTransitionView):
    transition = staticmethod(withdraw_job)


@_transition_schema("jobs_post_create")
class JobPostView(JobTransitionView):
    transition = staticmethod(post_job)


@_transition_schema("jobs_close_create")
class JobCloseView(JobTransitionView):
    transition = staticmethod(close_job)


@_transition_schema("jobs_reopen_create")
class JobReopenView(JobTransitionView):
    transition = staticmethod(reopen_job)


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
                    "id": application.id,
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


def _recruiter(view: APIView) -> RecruiterProfile:
    profile = approved_recruiter(view.request.user)
    assert profile is not None, "IsApprovedRecruiter must run before this view"
    return profile


@extend_schema_view(get=extend_schema(operation_id="marketplace_jobs_list", tags=["marketplace"]))
class MarketplaceJobListView(ListAPIView[Job]):
    queryset = Job.objects.none()
    permission_classes = (IsApprovedRecruiter,)
    serializer_class = MarketplaceJobListItemSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = MarketplaceFilterSet

    def get_queryset(self) -> QuerySet[Job]:
        recruiter = _recruiter(self)
        return annotate_marketplace(marketplace_queryset(recruiter), recruiter)


@extend_schema_view(
    get=extend_schema(operation_id="marketplace_jobs_retrieve", tags=["marketplace"])
)
class MarketplaceJobDetailView(RetrieveAPIView[Job]):
    queryset = Job.objects.none()
    permission_classes = (IsApprovedRecruiter,)
    serializer_class = MarketplaceJobSerializer

    def get_queryset(self) -> QuerySet[Job]:
        # Also what they were invited to, which the list leaves out.
        recruiter = _recruiter(self)
        return annotate_marketplace(
            jobs_offered_to(recruiter).select_related("company", "office"), recruiter
        )

    def get_serializer_context(self) -> dict[str, Any]:
        return {**super().get_serializer_context(), "recruiter": _recruiter(self)}


class MarketplaceActionView(APIView):
    permission_classes = (IsApprovedRecruiter,)


@extend_schema_view(
    post=extend_schema(
        operation_id="marketplace_jobs_join_create",
        tags=["marketplace"],
        request=None,
        responses=JoinResultSerializer,
    )
)
class MarketplaceJoinView(MarketplaceActionView):
    def post(self, request: Request, pk: str) -> Response:
        assignment, created, channel = join_marketplace_job(pk, _recruiter(self))
        payload = {"assignment_id": assignment.pk, "channel_id": channel.pk, "joined": True}
        return Response(
            JoinResultSerializer(payload).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema_view(
    post=extend_schema(
        operation_id="marketplace_jobs_leave_create",
        tags=["marketplace"],
        request=None,
        responses=LeaveResultSerializer,
    )
)
class MarketplaceLeaveView(MarketplaceActionView):
    def post(self, request: Request, pk: str) -> Response:
        recruiter = _recruiter(self)
        # Any job they are on, closed or deleted too: they may always step off it.
        job = get_object_or_404(jobs_joined_by(recruiter), pk=pk)
        assignment = get_object_or_404(
            JobAssignment, job=job, recruiter=recruiter, left_at__isnull=True
        )
        assignment.left_at = timezone.now()
        assignment.save(update_fields=["left_at"])
        payload = {"assignment_id": assignment.pk, "joined": False, "left_at": assignment.left_at}
        return Response(LeaveResultSerializer(payload).data)


class CanParseJd(BasePermission):
    """Whoever may post a job may read a JD into the form; recruiters and staff may not."""

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = request.user
        return isinstance(user, User) and user.has_perm("jobs.add_job")


class JdParsingOff(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Reading job descriptions is turned off. Fill the form in yourself."
    default_code = "jd_parsing_off"


@extend_schema_view(
    get=extend_schema(
        operation_id="jd_parses_availability_retrieve",
        tags=["jobs"],
        responses=JdParseAvailabilitySerializer,
    )
)
class JdParseAvailabilityView(APIView):
    """Create new job only shows the JD upload when the server can read one."""

    permission_classes = (CanParseJd,)

    def get(self, request: Request) -> Response:
        return Response({"enabled": jd_parsing_enabled()})


class JdParseCreateView(APIView):
    permission_classes = (CanParseJd,)
    parser_classes = (MultiPartParser,)
    throttle_scope = "jd_parse"

    @extend_schema(
        tags=["jobs"],
        operation_id="jd_parses_create",
        request={"multipart/form-data": JdParseCreateSerializer},
        responses={202: JdParseSerializer},
    )
    def post(self, request: Request) -> Response:
        if not jd_parsing_enabled():
            raise JdParsingOff
        form = JdParseCreateSerializer(
            data=request.data,
            context={"user": request.user, "company": company_of(_user(self))},
        )
        form.is_valid(raise_exception=True)
        attempt = form.save()
        transaction.on_commit(lambda: parse_jd.delay(str(attempt.pk)))
        return Response(JdParseSerializer(attempt).data, status=status.HTTP_202_ACCEPTED)


@extend_schema_view(
    get=extend_schema(tags=["jobs"], operation_id="jd_parses_retrieve", responses=JdParseSerializer)
)
class JdParseDetailView(RetrieveAPIView[JdParse]):
    """Only the uploader polls a parse; anyone else gets 404."""

    permission_classes = (CanParseJd,)
    serializer_class = JdParseSerializer
    queryset = JdParse.objects.none()

    def get_queryset(self) -> QuerySet[JdParse]:
        return JdParse.objects.filter(uploaded_by=_user(self))


@extend_schema_view(
    get=extend_schema(
        operation_id="jobs_change_requests_list",
        tags=["jobs"],
        responses=ChangeRequestSerializer,
        parameters=[
            OpenApiParameter(
                "status",
                str,
                enum=["pending", "approved", "rejected", "withdrawn", "all"],
                description="Which proposals to list. Defaults to pending.",
            )
        ],
    ),
    post=extend_schema(
        operation_id="jobs_change_requests_create",
        tags=["jobs"],
        description="Propose an edit, close, reopen or delete of the job to the company admins.",
        request=ChangeRequestCreateSerializer,
        responses={201: ChangeRequestSerializer},
    ),
)
class JobChangeRequestListCreateView(ListAPIView[JobChangeRequest]):
    """Proposals on one job that the viewer may see (admins all, a requester their own).

    Pending by default; ``?status=`` picks another state or ``all``.
    """

    serializer_class = ChangeRequestSerializer
    queryset = JobChangeRequest.objects.none()

    def get_queryset(self) -> QuerySet[JobChangeRequest]:
        job = get_object_or_404(jobs_visible_to(_user(self)), pk=self.kwargs["pk"])
        wanted = self.request.query_params.get("status", ChangeStatus.PENDING.value)
        if wanted != "all" and wanted not in ChangeStatus.values:
            raise ParseError("Unknown status.")
        queryset = change_requests_visible_to(_user(self)).filter(job=job)
        if wanted != "all":
            queryset = queryset.filter(status=wanted)
        return queryset.select_related("job", "requested_by", "reviewed_by").order_by("-created_at")

    def post(self, request: Request, pk: str) -> Response:
        job = get_object_or_404(jobs_visible_to(_user(self)), pk=pk)
        form = ChangeRequestCreateSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        change = propose_change(
            job,
            actor=_actor(self),
            kind=form.validated_data["kind"],
            changes=form.validated_data.get("changes"),
        )
        return Response(ChangeRequestSerializer(change).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        operation_id="company_change_requests_list",
        tags=["jobs"],
        responses=ChangeRequestSerializer,
    )
)
class CompanyChangeRequestListView(ListAPIView[JobChangeRequest]):
    """Every pending proposal in the company: the admin's inbox."""

    serializer_class = ChangeRequestSerializer
    queryset = JobChangeRequest.objects.none()

    def get_queryset(self) -> QuerySet[JobChangeRequest]:
        if not is_admin(_user(self)):
            raise PermissionDenied("Only a company admin can see every proposal.")
        return (
            change_requests_visible_to(_user(self))
            .filter(status=ChangeStatus.PENDING)
            .select_related("job", "requested_by", "reviewed_by")
        )


class ChangeRequestActionView(APIView):
    """404 unless the viewer may see the request; the decision functions check the rest."""

    def get_change(self, pk: str) -> JobChangeRequest:
        return get_object_or_404(change_requests_visible_to(_user(self)), pk=pk)

    def respond(self, change: JobChangeRequest) -> Response:
        change = JobChangeRequest.objects.select_related("job", "requested_by", "reviewed_by").get(
            pk=change.pk
        )
        return Response(ChangeRequestSerializer(change).data)


@extend_schema_view(
    post=extend_schema(
        operation_id="change_requests_approve_create",
        tags=["jobs"],
        request=None,
        responses=ChangeRequestSerializer,
    )
)
class ChangeRequestApproveView(ChangeRequestActionView):
    def post(self, request: Request, pk: str) -> Response:
        change = self.get_change(pk)
        approve_change(change, actor=_actor(self))
        return self.respond(change)


@extend_schema_view(
    post=extend_schema(
        operation_id="change_requests_reject_create",
        tags=["jobs"],
        request=ChangeRequestDecisionSerializer,
        responses=ChangeRequestSerializer,
    )
)
class ChangeRequestRejectView(ChangeRequestActionView):
    def post(self, request: Request, pk: str) -> Response:
        change = self.get_change(pk)
        form = ChangeRequestDecisionSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        reject_change(change, actor=_actor(self), note=form.validated_data["note"])
        return self.respond(change)


@extend_schema_view(
    post=extend_schema(
        operation_id="change_requests_withdraw_create",
        tags=["jobs"],
        request=None,
        responses=ChangeRequestSerializer,
    )
)
class ChangeRequestWithdrawView(ChangeRequestActionView):
    def post(self, request: Request, pk: str) -> Response:
        change = self.get_change(pk)
        withdraw_change(change, actor=_actor(self))
        return self.respond(change)
