from django.db.models import QuerySet
from django.http import Http404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.generics import ListCreateAPIView, RetrieveAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.jobs.access import can_change_job, jobs_visible_to
from apps.jobs.models import Job, JobStatus
from apps.jobs.serializers import JobSerializer, PublicJobSerializer


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
    serializer_class = JobSerializer
    permission_classes = (CanAddJob,)

    def get_queryset(self) -> QuerySet[Job]:
        # Plan 05 layers filters, search, ordering and annotations on top of this.
        return jobs_visible_to(_user(self)).select_related("company", "office")


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
