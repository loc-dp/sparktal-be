import csv
import io
import logging
import shutil
import tempfile
import zipfile
from zoneinfo import ZoneInfo

from celery import shared_task
from django.core.files import File
from django.utils import timezone
from django.utils.text import slugify

from apps.candidates.models import Application, Stage
from apps.exports.constants import EXPORT_TTL
from apps.exports.models import CvExport, CvExportStatus
from apps.exports.naming import unique_entry_names

logger = logging.getLogger(__name__)

MANIFEST_NAME = "candidates.csv"
MANIFEST_HEADER = [
    "No.",
    "Candidate",
    "Current title",
    "Stage",
    "Recruiter",
    "Submitted at",
    "File name",
]
HCM = ZoneInfo("Asia/Ho_Chi_Minh")
COPY_CHUNK = 256 * 1024


class MissingCvError(RuntimeError):
    """An application points to a CV blob the storage backend no longer has."""


def applications_for(export: CvExport) -> list[Application]:
    queryset = Application.objects.filter(job_id=export.job_id).select_related(
        "candidate", "recruiter"
    )
    if export.application_ids:
        queryset = queryset.filter(id__in=export.application_ids)
    else:
        queryset = queryset.exclude(stage=Stage.REJECTED)
    return list(queryset.order_by("-submitted_at"))


def manifest_csv(applications: list[Application], names: list[str]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(MANIFEST_HEADER)
    for index, (application, name) in enumerate(zip(applications, names, strict=True), start=1):
        writer.writerow(
            [
                index,
                application.candidate.full_name,
                application.candidate.current_title,
                application.get_stage_display(),
                application.recruiter.nickname,
                application.submitted_at.astimezone(HCM).strftime("%d/%m/%Y %H:%M"),
                name,
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")


def copy_cv_into(archive: zipfile.ZipFile, application: Application, name: str) -> None:
    """Stream one CV through Django storage so local and object storage behave alike."""
    if not application.cv_file:
        raise MissingCvError(f"CV file is missing for {application.candidate.full_name}.")
    try:
        source = application.cv_file.open("rb")
    except (FileNotFoundError, OSError) as exc:
        raise MissingCvError(
            f"CV file is missing for {application.candidate.full_name} ({application.cv_filename})."
        ) from exc

    info = zipfile.ZipInfo(name, date_time=application.submitted_at.astimezone(HCM).timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    with source, archive.open(info, "w") as target:
        shutil.copyfileobj(source, target, COPY_CHUNK)


def zip_filename(export: CvExport) -> str:
    stamp = export.created_at.astimezone(HCM).strftime("%Y%m%d-%H%M")
    return f"{slugify(export.job.title) or 'cv'}-cvs-{stamp}.zip"


@shared_task(name="exports.build_cv_export")
def build_cv_export(export_id: str) -> str:
    export = CvExport.objects.select_related("job").get(pk=export_id)
    export.mark_running()
    try:
        applications = applications_for(export)
        if not applications:
            export.mark_failed("There are no applicants to export for this job.")
            return export.status

        names = unique_entry_names(applications)
        with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for application, name in zip(applications, names, strict=True):
                    copy_cv_into(archive, application, name)
                archive.writestr(MANIFEST_NAME, manifest_csv(applications, names))
            tmp.flush()
            tmp.seek(0)
            export.file.save(zip_filename(export), File(tmp), save=False)

        export.mark_done(file_count=len(applications))
    except MissingCvError as exc:
        export.mark_failed(str(exc))
    except Exception as exc:
        logger.exception("CV export %s failed", export_id)
        if export.file:
            export.file.delete(save=False)
        export.mark_failed(f"The export could not be built ({exc.__class__.__name__}).")
    return export.status


@shared_task(name="exports.purge_expired_cv_exports")
def purge_expired_cv_exports() -> int:
    """Delete expired exports and stale queued/running rows with their blobs."""
    now = timezone.now()
    removed = 0
    stale = CvExport.objects.filter(expires_at__lt=now) | CvExport.objects.filter(
        status__in=[CvExportStatus.QUEUED, CvExportStatus.RUNNING],
        created_at__lt=now - EXPORT_TTL,
    )
    for export in stale.distinct().iterator():
        if export.file:
            export.file.delete(save=False)
        export.delete()
        removed += 1
    return removed
