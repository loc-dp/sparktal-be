from datetime import timedelta

import pytest
from django.utils import timezone

from apps.exports.constants import EXPORT_TTL
from apps.exports.models import CvExport, CvExportStatus
from apps.exports.tests.factories import CvExportFactory
from apps.jobs.models import Job

pytestmark = pytest.mark.django_db


def test_a_new_export_is_queued_and_empty(job: Job) -> None:
    export = CvExport.objects.create(job=job, requested_by=job.created_by)

    assert export.status == CvExportStatus.QUEUED
    assert export.application_ids == []
    assert export.file_count == 0
    assert export.error == ""
    assert export.finished_at is None
    assert export.expires_at is None
    assert export.is_expired is False


def test_mark_done_sets_the_expiry_24_hours_out(job: Job) -> None:
    export = CvExportFactory(job=job)

    export.mark_done(file_count=3)

    assert export.status == CvExportStatus.DONE
    assert export.file_count == 3
    assert export.finished_at is not None
    assert export.expires_at is not None
    assert abs((export.expires_at - export.finished_at) - EXPORT_TTL) < timedelta(seconds=2)


def test_mark_failed_stores_a_readable_error(job: Job) -> None:
    export = CvExportFactory(job=job)

    export.mark_failed("CV file is missing for Nguyễn Thị Hằng (cv.pdf).")

    assert export.status == CvExportStatus.FAILED
    assert "Nguyễn Thị Hằng" in export.error
    assert export.finished_at is not None


def test_is_expired_flips_after_expires_at(job: Job) -> None:
    export = CvExportFactory(
        job=job,
        status=CvExportStatus.DONE,
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    assert export.is_expired is True
