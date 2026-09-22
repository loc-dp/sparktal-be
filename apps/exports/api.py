from typing import Any, cast

from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import APIException, Throttled
from rest_framework.generics import RetrieveAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.candidates.api import job_or_404
from apps.core.http import content_disposition
from apps.exports.constants import RATE_LIMIT_MAX, RATE_LIMIT_WINDOW
from apps.exports.models import CvExport, CvExportStatus
from apps.exports.serializers import CvExportCreateSerializer, CvExportSerializer
from apps.exports.tasks import build_cv_export
from apps.exports.throttle import consume_export_quota
from apps.jobs.access import can_view_job

RATE_LIMITED = (
    f"You can start {RATE_LIMIT_MAX} CV exports every {RATE_LIMIT_WINDOW // 60} minutes. "
    "Try again in a moment."
)


@extend_schema_view(
    post=extend_schema(
        tags=["exports"],
        operation_id="jobs_cv_exports_create",
        request=CvExportCreateSerializer,
        responses={202: CvExportSerializer},
    )
)
class CvExportCreateView(APIView):
    def post(self, request: Request, job_id: Any) -> Response:
        job = job_or_404(request, job_id)
        user = cast(User, request.user)

        form = CvExportCreateSerializer(data=request.data, context={"job": job})
        form.is_valid(raise_exception=True)

        wait = consume_export_quota(user)
        if wait is not None:
            raise Throttled(wait=wait, detail=RATE_LIMITED)

        export = CvExport.objects.create(
            job=job,
            requested_by=user,
            application_ids=form.validated_data["application_ids"],
        )
        transaction.on_commit(lambda: build_cv_export.delay(str(export.pk)))

        body = CvExportSerializer(export, context={"request": request}).data
        return Response(body, status=status.HTTP_202_ACCEPTED)


class ExportNotReady(APIException):
    status_code = 409
    default_detail = "The export is not ready yet."
    default_code = "export_not_ready"


class ExportGone(APIException):
    status_code = 410
    default_detail = "This export has expired. Start a new export."
    default_code = "export_expired"


def export_or_404(request: Request, pk: Any) -> CvExport:
    export = get_object_or_404(CvExport.objects.select_related("job"), pk=pk)
    if not can_view_job(cast(User, request.user), export.job):
        raise Http404
    return export


@extend_schema_view(
    get=extend_schema(
        tags=["exports"], operation_id="cv_exports_retrieve", responses=CvExportSerializer
    )
)
class CvExportDetailView(RetrieveAPIView[CvExport]):
    serializer_class = CvExportSerializer

    def get_object(self) -> CvExport:
        return export_or_404(self.request, self.kwargs["pk"])


@extend_schema_view(
    get=extend_schema(
        tags=["exports"],
        operation_id="cv_exports_download_retrieve",
        responses={(200, "application/zip"): OpenApiTypes.BINARY},
    )
)
class CvExportDownloadView(APIView):
    def get(self, request: Request, pk: Any) -> FileResponse:
        export = export_or_404(request, pk)
        if export.status != CvExportStatus.DONE:
            raise ExportNotReady
        if export.is_expired or not export.file:
            raise ExportGone
        try:
            handle = export.file.open("rb")
        except (FileNotFoundError, OSError) as exc:
            raise ExportGone from exc

        CvExport.objects.filter(pk=export.pk).update(
            downloaded_at=timezone.now(), downloaded_by=cast(User, request.user)
        )
        response = FileResponse(handle, content_type="application/zip")
        stored_name = export.file.name or "cv-export.zip"
        response["Content-Disposition"] = content_disposition(stored_name.split("/")[-1])
        response["X-Content-Type-Options"] = "nosniff"
        return response
