import csv
import io
import zipfile
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.candidates.models import Stage
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.exports.models import CvExport, CvExportStatus
from apps.exports.tasks import MANIFEST_NAME, build_cv_export, purge_expired_cv_exports
from apps.exports.tests.factories import CvExportFactory
from apps.jobs.models import Job

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_applications(job: Job, recruiter_profile: Any) -> list[Any]:
    return [
        ApplicationFactory(
            job=job,
            recruiter=recruiter_profile,
            stage=Stage.NEW,
            cv_filename="hang.pdf",
            candidate=CandidateFactory(
                owner=recruiter_profile,
                full_name="Nguyễn Thị Hằng",
                current_title="Backend Engineer",
            ),
        ),
        ApplicationFactory(
            job=job,
            recruiter=recruiter_profile,
            stage=Stage.INTERVIEW,
            cv_filename="khoa.pdf",
            candidate=CandidateFactory(
                owner=recruiter_profile,
                full_name="Trần Minh Khoa",
                current_title="Staff Engineer",
            ),
        ),
    ]


def entries(export: CvExport) -> list[str]:
    with export.file.open("rb") as handle, zipfile.ZipFile(handle) as archive:
        return archive.namelist()


def manifest_rows(export: CvExport) -> list[list[str]]:
    with export.file.open("rb") as handle, zipfile.ZipFile(handle) as archive:
        raw = archive.read(MANIFEST_NAME)
    assert raw.startswith(b"\xef\xbb\xbf"), "Excel needs a UTF-8 BOM"
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))


def test_the_zip_holds_one_entry_per_cv_plus_the_manifest(
    job: Job, company_ta: Any, two_applications: list[Any]
) -> None:
    export = CvExportFactory(
        job=job,
        requested_by=company_ta,
        application_ids=[application.id for application in two_applications],
    )

    build_cv_export(str(export.pk))

    export.refresh_from_db()
    assert export.status == CvExportStatus.DONE
    assert export.file_count == 2
    assert export.error == ""
    assert export.expires_at - export.finished_at == timedelta(hours=24)
    names = entries(export)
    assert MANIFEST_NAME in names
    assert sorted(name for name in names if name != MANIFEST_NAME) == [
        "01 - Trần Minh Khoa - NorthStar Talent.pdf",
        "02 - Nguyễn Thị Hằng - NorthStar Talent.pdf",
    ]


def test_the_manifest_lists_every_candidate_in_asia_ho_chi_minh(
    job: Job, company_ta: Any, two_applications: list[Any]
) -> None:
    export = CvExportFactory(
        job=job,
        requested_by=company_ta,
        application_ids=[application.id for application in two_applications],
    )

    build_cv_export(str(export.pk))
    export.refresh_from_db()

    rows = manifest_rows(export)
    assert rows[0] == [
        "No.",
        "Candidate",
        "Current title",
        "Stage",
        "Recruiter",
        "Submitted at",
        "File name",
    ]
    assert len(rows) == 3
    assert rows[1][1] == "Trần Minh Khoa"
    assert rows[1][3] == "Interview"
    assert rows[1][4] == "NorthStar Talent"
    local = timezone.localtime(two_applications[1].submitted_at)
    assert rows[1][5] == local.strftime("%d/%m/%Y %H:%M")
    assert rows[1][6] == "01 - Trần Minh Khoa - NorthStar Talent.pdf"


def test_an_empty_selection_exports_every_non_rejected_application(
    job: Job,
    company_ta: Any,
    two_applications: list[Any],
    recruiter_profile: Any,
) -> None:
    ApplicationFactory(job=job, recruiter=recruiter_profile, stage=Stage.REJECTED)
    export = CvExportFactory(job=job, requested_by=company_ta, application_ids=[])

    build_cv_export(str(export.pk))

    export.refresh_from_db()
    assert export.file_count == 2


def test_two_candidates_with_the_same_name_still_get_distinct_entries(
    job: Job, company_ta: Any, recruiter_profile: Any
) -> None:
    same = [
        ApplicationFactory(
            job=job,
            recruiter=recruiter_profile,
            cv_filename="cv.pdf",
            candidate=CandidateFactory(owner=recruiter_profile, full_name="Lê Văn A"),
        )
        for _ in range(2)
    ]
    export = CvExportFactory(
        job=job, requested_by=company_ta, application_ids=[item.id for item in same]
    )

    build_cv_export(str(export.pk))
    export.refresh_from_db()

    names = [name for name in entries(export) if name != MANIFEST_NAME]
    assert len(set(names)) == 2
    assert names[0].endswith("Lê Văn A - NorthStar Talent.pdf")


def test_a_missing_cv_file_fails_the_export_with_a_readable_error(
    job: Job, company_ta: Any, two_applications: list[Any]
) -> None:
    broken = two_applications[0]
    broken.cv_file.storage.delete(broken.cv_file.name)
    export = CvExportFactory(job=job, requested_by=company_ta, application_ids=[broken.id])

    build_cv_export(str(export.pk))

    export.refresh_from_db()
    assert export.status == CvExportStatus.FAILED
    assert "Nguyễn Thị Hằng" in export.error
    assert not export.file


def test_purge_removes_expired_exports_and_their_blobs(
    job: Job, company_ta: Any, two_applications: list[Any]
) -> None:
    export = CvExportFactory(
        job=job,
        requested_by=company_ta,
        application_ids=[two_applications[0].id],
    )
    build_cv_export(str(export.pk))
    export.refresh_from_db()
    storage, name = export.file.storage, export.file.name
    CvExport.objects.filter(pk=export.pk).update(expires_at=timezone.now() - timedelta(minutes=1))

    assert purge_expired_cv_exports() == 1
    assert not CvExport.objects.filter(pk=export.pk).exists()
    assert not storage.exists(name)


def test_purge_keeps_live_exports(job: Job, company_ta: Any, two_applications: list[Any]) -> None:
    export = CvExportFactory(
        job=job, requested_by=company_ta, application_ids=[two_applications[0].id]
    )
    build_cv_export(str(export.pk))

    assert purge_expired_cv_exports() == 0
    assert CvExport.objects.filter(pk=export.pk).exists()
