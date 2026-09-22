import zipfile
from datetime import timedelta
from io import BytesIO
from typing import Any, cast

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.candidates.tests.factories import ApplicationFactory
from apps.exports.models import CvExport, CvExportStatus
from apps.exports.tasks import MANIFEST_NAME, build_cv_export
from apps.exports.tests.factories import CvExportFactory
from apps.jobs.access import grant_creator_perms
from apps.jobs.models import Job

pytestmark = pytest.mark.django_db


@pytest.fixture
def finished_export(job: Job, company_ta: User, recruiter_profile: Any) -> CvExport:
    application = ApplicationFactory(job=job, recruiter=recruiter_profile)
    export = cast(
        CvExport,
        CvExportFactory(job=job, requested_by=company_ta, application_ids=[application.id]),
    )
    build_cv_export(str(export.pk))
    export.refresh_from_db()
    return export


def test_status_endpoint_reports_done_with_a_download_url(
    client_for: Any, company_ta: User, finished_export: CvExport
) -> None:
    response = client_for(company_ta).get(f"/api/v1/cv-exports/{finished_export.pk}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["file_count"] == 1
    assert body["download_url"].endswith(f"/api/v1/cv-exports/{finished_export.pk}/download")


def test_status_endpoint_reports_failure_without_a_url(
    client_for: Any, company_ta: User, job: Job
) -> None:
    export = CvExportFactory(job=job, requested_by=company_ta)
    export.mark_failed("CV file is missing for Lê Văn A (cv.pdf).")

    body = client_for(company_ta).get(f"/api/v1/cv-exports/{export.pk}").json()

    assert body["status"] == "failed"
    assert body["download_url"] is None
    assert "Lê Văn A" in body["error"]


def test_download_streams_the_zip_and_records_who_took_it(
    client_for: Any, company_ta: User, finished_export: CvExport
) -> None:
    response = client_for(company_ta).get(f"/api/v1/cv-exports/{finished_export.pk}/download")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert "attachment;" in response["Content-Disposition"]
    assert response["X-Content-Type-Options"] == "nosniff"
    with zipfile.ZipFile(BytesIO(b"".join(response.streaming_content))) as archive:
        assert MANIFEST_NAME in archive.namelist()
    finished_export.refresh_from_db()
    assert finished_export.downloaded_by == company_ta
    assert finished_export.downloaded_at is not None


@pytest.mark.parametrize("status", [CvExportStatus.QUEUED, CvExportStatus.RUNNING])
def test_downloading_before_it_is_ready_is_409(
    client_for: Any, company_ta: User, job: Job, status: str
) -> None:
    export = CvExportFactory(job=job, requested_by=company_ta, status=status)

    response = client_for(company_ta).get(f"/api/v1/cv-exports/{export.pk}/download")

    assert response.status_code == 409
    assert "not ready" in response.json()["detail"].lower()


def test_downloading_a_failed_export_is_409(client_for: Any, company_ta: User, job: Job) -> None:
    export = CvExportFactory(job=job, requested_by=company_ta)
    export.mark_failed("boom")

    assert client_for(company_ta).get(f"/api/v1/cv-exports/{export.pk}/download").status_code == 409


def test_downloading_an_expired_export_is_410(
    client_for: Any, company_ta: User, finished_export: CvExport
) -> None:
    CvExport.objects.filter(pk=finished_export.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    response = client_for(company_ta).get(f"/api/v1/cv-exports/{finished_export.pk}/download")

    assert response.status_code == 410
    assert "expired" in response.json()["detail"].lower()


def test_permission_matrix(
    client_for: Any,
    api_client: Any,
    job: Job,
    company_admin: User,
    company_hm: User,
    other_company_admin: User,
    recruiter_user: User,
    finished_export: CvExport,
) -> None:
    status_url = f"/api/v1/cv-exports/{finished_export.pk}"
    download_url = f"{status_url}/download"

    assert client_for(company_admin).get(status_url).status_code == 200
    assert client_for(company_admin).get(download_url).status_code == 200
    assert client_for(company_hm).get(status_url).status_code == 404
    assert client_for(company_hm).get(download_url).status_code == 404
    grant_creator_perms(job, company_hm)
    assert client_for(company_hm).get(status_url).status_code == 200
    assert client_for(other_company_admin).get(status_url).status_code == 404
    assert client_for(other_company_admin).get(download_url).status_code == 404
    assert client_for(recruiter_user).get(status_url).status_code == 404
    assert api_client.get(status_url).status_code == 403
