from typing import Any
from uuid import UUID

from rest_framework import serializers

from apps.candidates.models import Application, Stage
from apps.exports.constants import MAX_EXPORT_FILES
from apps.exports.models import CvExport, CvExportStatus

TOO_MANY = (
    f"You can export at most {MAX_EXPORT_FILES} CVs at a time. "
    "Narrow the selection with the stage filter."
)


class CvExportCreateSerializer(serializers.Serializer[Any]):
    application_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True, default=list
    )

    def validate_application_ids(self, value: list[UUID]) -> list[UUID]:
        job = self.context["job"]
        chosen = list(dict.fromkeys(value))

        if not chosen:
            total = Application.objects.filter(job=job).exclude(stage=Stage.REJECTED).count()
            if total == 0:
                raise serializers.ValidationError("This job has no applicants to export.")
            if total > MAX_EXPORT_FILES:
                raise serializers.ValidationError(TOO_MANY)
            return []

        if len(chosen) > MAX_EXPORT_FILES:
            raise serializers.ValidationError(TOO_MANY)

        known = set(Application.objects.filter(job=job, id__in=chosen).values_list("id", flat=True))
        unknown = sorted(str(item) for item in chosen if item not in known)
        if unknown:
            raise serializers.ValidationError(
                f"These applications do not belong to this job: {', '.join(unknown)}."
            )
        return chosen


class CvExportSerializer(serializers.ModelSerializer[CvExport]):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = CvExport
        fields = (
            "id",
            "status",
            "file_count",
            "error",
            "created_at",
            "finished_at",
            "expires_at",
            "download_url",
        )
        read_only_fields = fields

    def get_download_url(self, export: CvExport) -> str | None:
        if export.status != CvExportStatus.DONE or export.is_expired:
            return None
        request = self.context.get("request")
        path = f"/api/v1/cv-exports/{export.pk}/download"
        return request.build_absolute_uri(path) if request else path
