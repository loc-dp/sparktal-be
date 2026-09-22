from typing import Any, cast

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.generics import (
    ListAPIView,
    ListCreateAPIView,
    RetrieveAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import BaseSerializer
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.candidates.models import Application, Candidate, CvParse, Stage
from apps.candidates.search import parse_boolean
from apps.candidates.serializers import (
    ApplicationListSerializer,
    CandidateCvSerializer,
    CandidateSerializer,
    CandidateWriteSerializer,
    CvParseCreateSerializer,
    CvParseSerializer,
)
from apps.candidates.tasks import parse_cv
from apps.core.http import content_disposition
from apps.core.pagination import DefaultPagination
from apps.jobs.access import can_view_job
from apps.jobs.models import Job
from apps.recruiters.access import IsApprovedRecruiter, approved_recruiter


def job_or_404(request: Request, job_id: Any) -> Job:
    """Return 404 so callers cannot probe jobs outside their permission scope."""
    job = get_object_or_404(Job, pk=job_id)
    if not can_view_job(cast(User, request.user), job):
        raise Http404
    return job


@extend_schema_view(
    get=extend_schema(
        tags=["applications"],
        operation_id="jobs_applications_list",
        parameters=[
            OpenApiParameter("stage", OpenApiTypes.STR, enum=[value for value, _ in Stage.choices]),
            OpenApiParameter(
                "search", OpenApiTypes.STR, description="Candidate name or current title."
            ),
        ],
    )
)
class JobApplicationListView(ListAPIView[Application]):
    serializer_class = ApplicationListSerializer

    def get_queryset(self) -> QuerySet[Application]:
        job = job_or_404(self.request, self.kwargs["job_id"])
        queryset = Application.objects.filter(job=job).select_related("candidate", "recruiter")

        stage = self.request.query_params.get("stage")
        if stage:
            if stage not in Stage.values:
                raise ValidationError({"stage": [f"Unknown stage '{stage}'."]})
            queryset = queryset.filter(stage=stage)

        search = (self.request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(candidate__full_name__icontains=search)
                | Q(candidate__current_title__icontains=search)
            )

        return queryset.order_by("-submitted_at")


@extend_schema_view(
    get=extend_schema(
        tags=["applications"],
        operation_id="applications_cv_retrieve",
        responses={(200, "application/octet-stream"): OpenApiTypes.BINARY},
    )
)
class ApplicationCvView(APIView):
    """Stream a single CV through the configured Django storage backend."""

    def get(self, request: Request, pk: Any) -> FileResponse:
        application = get_object_or_404(
            Application.objects.select_related("job", "candidate"), pk=pk
        )
        if not can_view_job(cast(User, request.user), application.job):
            raise Http404
        if not application.cv_file:
            raise Http404
        try:
            handle = application.cv_file.open("rb")
        except (FileNotFoundError, OSError) as exc:
            raise Http404 from exc

        filename = application.cv_filename or "cv.pdf"
        response = FileResponse(handle, content_type="application/octet-stream")
        response["Content-Disposition"] = content_disposition(filename)
        response["X-Content-Type-Options"] = "nosniff"
        return response


class CvParseCreateView(APIView):
    permission_classes = (IsApprovedRecruiter,)
    parser_classes = (MultiPartParser,)

    @extend_schema(
        tags=["candidates"],
        operation_id="cv_parses_create",
        request={"multipart/form-data": CvParseCreateSerializer},
        responses={202: CvParseSerializer},
    )
    def post(self, request: Request) -> Response:
        recruiter = approved_recruiter(cast(User, request.user))
        form = CvParseCreateSerializer(data=request.data, context={"recruiter": recruiter})
        form.is_valid(raise_exception=True)
        attempt = form.save()
        transaction.on_commit(lambda: parse_cv.delay(str(attempt.pk)))
        return Response(CvParseSerializer(attempt).data, status=status.HTTP_202_ACCEPTED)


@extend_schema_view(
    get=extend_schema(
        tags=["candidates"], operation_id="cv_parses_retrieve", responses=CvParseSerializer
    )
)
class CvParseDetailView(RetrieveAPIView[CvParse]):
    permission_classes = (IsApprovedRecruiter,)
    serializer_class = CvParseSerializer
    queryset = CvParse.objects.none()

    def get_queryset(self) -> QuerySet[CvParse]:
        return CvParse.objects.filter(owner=approved_recruiter(cast(User, self.request.user)))


ORDERING_FIELDS = (
    "full_name",
    "created_at",
    "updated_at",
    "years_experience",
    "expected_salary",
)


class CandidateInUse(APIException):
    status_code = 409
    default_detail = "This candidate has already been submitted to a job and cannot be deleted."
    default_code = "candidate_in_use"


def pool_of(request: Request) -> QuerySet[Candidate]:
    recruiter = approved_recruiter(cast(User, request.user))
    return Candidate.objects.filter(owner=recruiter).annotate(submitted_count=Count("applications"))


@extend_schema_view(
    get=extend_schema(
        tags=["candidates"],
        operation_id="candidates_list",
        parameters=[
            OpenApiParameter("q", OpenApiTypes.STR),
            OpenApiParameter("is_expat", OpenApiTypes.BOOL),
            OpenApiParameter("skills", OpenApiTypes.STR),
            OpenApiParameter(
                "ordering",
                OpenApiTypes.STR,
                enum=sorted([*ORDERING_FIELDS, *(f"-{field}" for field in ORDERING_FIELDS)]),
            ),
        ],
    ),
    post=extend_schema(
        tags=["candidates"],
        operation_id="candidates_create",
        request=CandidateWriteSerializer,
        responses={201: CandidateSerializer},
    ),
)
class CandidateListCreateView(ListCreateAPIView[Candidate]):
    permission_classes = (IsApprovedRecruiter,)
    pagination_class = DefaultPagination
    queryset = Candidate.objects.none()

    def get_serializer_class(self) -> type[BaseSerializer[Candidate]]:
        return CandidateWriteSerializer if self.request.method == "POST" else CandidateSerializer

    def get_serializer_context(self) -> dict[str, Any]:
        return {
            **super().get_serializer_context(),
            "recruiter": approved_recruiter(cast(User, self.request.user)),
        }

    def get_queryset(self) -> QuerySet[Candidate]:
        queryset = pool_of(self.request)
        params = self.request.query_params
        try:
            search = parse_boolean(params.get("q") or "")
        except DjangoValidationError as error:
            raise ValidationError({"q": error.messages}) from error
        if search is not None:
            queryset = queryset.filter(search_vector=search)
        is_expat = params.get("is_expat")
        if is_expat is not None:
            queryset = queryset.filter(is_expat=is_expat.lower() in ("1", "true", "yes"))
        skills = [
            skill.strip() for skill in (params.get("skills") or "").split(",") if skill.strip()
        ]
        if skills:
            queryset = queryset.filter(skills__overlap=skills)
        ordering = params.get("ordering") or "full_name"
        if ordering.lstrip("-") not in ORDERING_FIELDS:
            raise ValidationError({"ordering": [f"Cannot order by '{ordering}'."]})
        return queryset.order_by(ordering)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        form = self.get_serializer(data=request.data)
        form.is_valid(raise_exception=True)
        candidate = form.save()
        return Response(
            CandidateSerializer(pool_of(request).get(pk=candidate.pk)).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    get=extend_schema(
        tags=["candidates"], operation_id="candidates_retrieve", responses=CandidateSerializer
    ),
    patch=extend_schema(
        tags=["candidates"],
        operation_id="candidates_partial_update",
        request=CandidateWriteSerializer,
        responses={200: CandidateSerializer},
    ),
    delete=extend_schema(
        tags=["candidates"], operation_id="candidates_destroy", responses={204: None}
    ),
)
class CandidateDetailView(RetrieveUpdateDestroyAPIView[Candidate]):
    permission_classes = (IsApprovedRecruiter,)
    http_method_names = ("get", "patch", "delete", "head", "options")
    queryset = Candidate.objects.none()

    def get_queryset(self) -> QuerySet[Candidate]:
        return pool_of(self.request)

    def get_serializer_class(self) -> type[BaseSerializer[Candidate]]:
        return CandidateWriteSerializer if self.request.method == "PATCH" else CandidateSerializer

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        candidate = self.get_object()
        form = CandidateWriteSerializer(candidate, data=request.data, partial=True)
        form.is_valid(raise_exception=True)
        form.save()
        return Response(CandidateSerializer(self.get_queryset().get(pk=candidate.pk)).data)

    def perform_destroy(self, instance: Candidate) -> None:
        if instance.applications.exists():
            raise CandidateInUse
        instance.delete()


class CandidateCvView(APIView):
    permission_classes = (IsApprovedRecruiter,)
    parser_classes = (MultiPartParser,)

    @extend_schema(
        tags=["candidates"],
        operation_id="candidates_cv_upload",
        request={"multipart/form-data": CandidateCvSerializer},
        responses={200: CandidateSerializer},
    )
    def put(self, request: Request, pk: Any) -> Response:
        candidate = get_object_or_404(pool_of(request), pk=pk)
        form = CandidateCvSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        upload = form.validated_data["file"]
        previous_name = candidate.cv_file.name
        candidate.cv_file = upload
        candidate.cv_filename = upload.name or "cv.pdf"
        candidate.save(update_fields=("cv_file", "cv_filename", "updated_at"))
        if previous_name and previous_name != candidate.cv_file.name:
            storage = candidate.cv_file.storage
            transaction.on_commit(lambda: storage.delete(previous_name))
        return Response(CandidateSerializer(pool_of(request).get(pk=pk)).data)

    @extend_schema(
        tags=["candidates"],
        operation_id="candidates_cv_retrieve",
        responses={(200, "application/octet-stream"): OpenApiTypes.BINARY},
    )
    def get(self, request: Request, pk: Any) -> FileResponse:
        candidate = get_object_or_404(pool_of(request), pk=pk)
        if not candidate.cv_file:
            raise Http404
        try:
            handle = candidate.cv_file.open("rb")
        except (FileNotFoundError, OSError) as exc:
            raise Http404 from exc
        response = FileResponse(handle, content_type="application/octet-stream")
        response["Content-Disposition"] = content_disposition(candidate.cv_filename or "cv.pdf")
        response["X-Content-Type-Options"] = "nosniff"
        return response
