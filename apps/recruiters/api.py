from django.contrib.auth import login
from django.db.models import Count, Q, QuerySet
from django.http import Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import serializers, status
from rest_framework.generics import ListAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.candidates.models import Application, Stage
from apps.jobs.access import jobs_joined_by
from apps.jobs.marketplace import annotate_joined_jobs, marketplace_queryset
from apps.jobs.models import Job
from apps.recruiters.access import IsApprovedRecruiter, approved_recruiter, recruiter_profile_of
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.serializers import (
    NicknameCheckSerializer,
    RecruiterJobSerializer,
    RecruiterOverviewSerializer,
    RecruiterProfileSerializer,
    SignupResultSerializer,
    SignupSerializer,
    clean_nickname,
    nickname_is_free,
)

LOGIN_BACKEND = "allauth.account.auth_backends.AuthenticationBackend"


@extend_schema_view(
    post=extend_schema(
        tags=["recruiters"],
        operation_id="recruiters_signup_create",
        request=SignupSerializer,
        responses={201: SignupResultSerializer},
    )
)
class SignupView(APIView):
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "signup"

    def post(self, request: Request) -> Response:
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = serializer.save()
        login(request._request, profile.user, backend=LOGIN_BACKEND)
        return Response(
            {"id": profile.pk, "nickname": profile.nickname, "status": profile.status},
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    get=extend_schema(
        tags=["recruiters"],
        operation_id="recruiters_nickname_check_retrieve",
        parameters=[OpenApiParameter("nickname", OpenApiTypes.STR, required=True)],
        responses=NicknameCheckSerializer,
    )
)
class NicknameCheckView(APIView):
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)
    throttle_scope = "nickname"

    def get(self, request: Request) -> Response:
        try:
            candidate = clean_nickname(request.query_params.get("nickname", ""))
        except serializers.ValidationError:
            return Response({"available": False})
        return Response({"available": nickname_is_free(candidate)})


@extend_schema_view(
    get=extend_schema(
        tags=["recruiters"],
        operation_id="recruiters_me_retrieve",
        responses=RecruiterProfileSerializer,
    ),
    patch=extend_schema(
        tags=["recruiters"],
        operation_id="recruiters_me_partial_update",
        request=RecruiterProfileSerializer,
        responses=RecruiterProfileSerializer,
    ),
)
class RecruiterMeView(RetrieveUpdateAPIView[RecruiterProfile]):
    serializer_class = RecruiterProfileSerializer
    http_method_names = ("get", "patch", "head", "options")

    def get_object(self) -> RecruiterProfile:
        profile = recruiter_profile_of(self.request.user)
        if profile is None:
            raise Http404
        return profile


def _recruiter(view: APIView) -> RecruiterProfile:
    profile = approved_recruiter(view.request.user)
    assert profile is not None, "IsApprovedRecruiter must run before this view"
    return profile


@extend_schema_view(get=extend_schema(operation_id="recruiters_jobs_list", tags=["recruiters"]))
class RecruiterJobsListView(ListAPIView[Job]):
    queryset = Job.objects.none()
    permission_classes = (IsApprovedRecruiter,)
    serializer_class = RecruiterJobSerializer

    def get_queryset(self) -> QuerySet[Job]:
        recruiter = _recruiter(self)
        return annotate_joined_jobs(
            jobs_joined_by(recruiter).select_related("company", "office"), recruiter
        )


SCREENED_STAGES = (Stage.SCREEN, Stage.INTERVIEW, Stage.OFFER, Stage.ONBOARDED)
WAITING_LIMIT = 4


@extend_schema_view(
    get=extend_schema(
        operation_id="recruiters_overview_retrieve",
        tags=["recruiters"],
        responses=RecruiterOverviewSerializer,
    )
)
class RecruiterOverviewView(APIView):
    permission_classes = (IsApprovedRecruiter,)

    def get(self, request: Request) -> Response:
        recruiter = _recruiter(self)
        joined = list(
            annotate_joined_jobs(
                jobs_joined_by(recruiter).select_related("company", "office"), recruiter
            )
        )
        waiting = [job for job in joined if job.submitted_count == 0]  # type: ignore[attr-defined]
        counts = Application.objects.filter(recruiter=recruiter).aggregate(
            cv_sent=Count("id"),
            cv_screened=Count("id", filter=Q(stage__in=SCREENED_STAGES)),
            cv_in_interviews=Count("id", filter=Q(stage=Stage.INTERVIEW)),
            offer=Count("id", filter=Q(stage=Stage.OFFER)),
        )
        payload = {
            **counts,
            "current_jobs": len(joined),
            "jobs_waiting_for_cv": len(waiting),
            "marketplace_open_count": marketplace_queryset(recruiter).count(),
            "waiting_jobs": [
                {
                    "id": job.id,
                    "title": job.title,
                    "company_name": job.company.name,
                    "location_label": job.location_label,
                    "work_model": job.work_model,
                    "salary_min": job.salary_min,
                    "salary_max": job.salary_max,
                    "bounty_type": job.bounty_type,
                    "bounty_pct": job.bounty_pct,
                    "bounty_amount": job.bounty_amount,
                    "published_at": job.published_at,
                }
                for job in waiting[:WAITING_LIMIT]
            ],
        }
        return Response(RecruiterOverviewSerializer(payload).data)
