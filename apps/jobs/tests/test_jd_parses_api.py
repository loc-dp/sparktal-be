from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.core.cache import cache
from django.core.files.base import ContentFile
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.models import User
from apps.companies.models import Company
from apps.core.testing.pdf import make_pdf
from apps.jobs.models import JdParse, JdParseStatus
from apps.jobs.tests.factories import JdParseFactory

pytestmark = pytest.mark.django_db

PARSES = "/api/v1/jd-parses"
AVAILABILITY = "/api/v1/jd-parses/availability"

ClientFor = Callable[[User], APIClient]


def jd_upload(name: str = "JD_Ke_toan_truong.pdf") -> ContentFile[bytes]:
    return ContentFile(make_pdf("Ke toan truong\nTrach nhiem"), name=name)


@pytest.fixture
def jd_on(settings: Any) -> Any:
    settings.ANTHROPIC_API_KEY = "test-key"
    settings.JD_PARSE_MODEL = "claude-sonnet-5-5"
    return settings


def test_availability_follows_the_env_settings(
    client_for: ClientFor, company_ta: User, settings: Any
) -> None:
    client = client_for(company_ta)
    assert client.get(AVAILABILITY).json() == {"enabled": False}

    settings.ANTHROPIC_API_KEY = "test-key"
    assert client.get(AVAILABILITY).json() == {"enabled": False}

    settings.JD_PARSE_MODEL = "claude-sonnet-5-5"
    assert client.get(AVAILABILITY).json() == {"enabled": True}


def test_uploading_a_jd_returns_202_and_a_queued_row(
    client_for: ClientFor, company_ta: User, company: Company, jd_on: Any
) -> None:
    response = client_for(company_ta).post(PARSES, {"file": jd_upload()}, format="multipart")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["fields"] == {}
    attempt = JdParse.objects.get(pk=body["id"])
    assert attempt.company == company
    assert attempt.uploaded_by == company_ta
    assert attempt.filename == "JD_Ke_toan_truong.pdf"


def test_a_company_admin_may_upload_too(
    client_for: ClientFor, company_admin: User, jd_on: Any
) -> None:
    response = client_for(company_admin).post(PARSES, {"file": jd_upload()}, format="multipart")

    assert response.status_code == 202


def test_a_hiring_manager_may_upload_because_they_may_post_jobs(
    client_for: ClientFor, company_hm: User, jd_on: Any
) -> None:
    # jobs.add_job is granted to every company member (apps/companies/rules.py).
    assert company_hm.has_perm("jobs.add_job")
    response = client_for(company_hm).post(PARSES, {"file": jd_upload()}, format="multipart")

    assert response.status_code == 202


def test_with_the_feature_off_an_upload_is_refused_and_nothing_stored(
    client_for: ClientFor, company_ta: User
) -> None:
    response = client_for(company_ta).post(PARSES, {"file": jd_upload()}, format="multipart")

    assert response.status_code == 503
    assert "turned off" in response.json()["detail"]
    assert JdParse.objects.count() == 0


@pytest.mark.parametrize(
    ("upload", "message"),
    [
        (ContentFile(b"\x89PNG\r\n\x1a\n", name="jd.png"), "PDF, DOCX or DOC"),
        (ContentFile(b"MZ not a pdf", name="jd.pdf"), "does not look like"),
    ],
    ids=("image", "renamed-binary"),
)
def test_a_wrong_file_is_rejected(
    client_for: ClientFor, company_ta: User, jd_on: Any, upload: Any, message: str
) -> None:
    response = client_for(company_ta).post(PARSES, {"file": upload}, format="multipart")

    assert response.status_code == 400
    assert message in str(response.json()["file"])
    assert JdParse.objects.count() == 0


def test_a_recruiter_is_refused(client_for: ClientFor, recruiter_user: User, jd_on: Any) -> None:
    client = client_for(recruiter_user)

    assert client.post(PARSES, {"file": jd_upload()}, format="multipart").status_code == 403
    assert client.get(AVAILABILITY).status_code == 403


def test_anonymous_is_refused(api_client: APIClient, jd_on: Any) -> None:
    assert api_client.post(PARSES, {"file": jd_upload()}, format="multipart").status_code == 403


def test_the_uploader_polls_their_parse(
    client_for: ClientFor, company_ta: User, company: Company
) -> None:
    attempt = JdParseFactory(
        company=company,
        uploaded_by=company_ta,
        status=JdParseStatus.DONE,
        fields={"title": "Kế toán trưởng"},
    )

    response = client_for(company_ta).get(f"{PARSES}/{attempt.pk}")

    assert response.status_code == 200
    assert response.json()["fields"] == {"title": "Kế toán trưởng"}


def test_a_colleague_cannot_poll_someone_elses_parse(
    client_for: ClientFor, company_ta: User, company_admin: User, company: Company
) -> None:
    attempt = JdParseFactory(company=company, uploaded_by=company_ta)

    assert client_for(company_admin).get(f"{PARSES}/{attempt.pk}").status_code == 404


def test_jd_uploads_are_rate_limited(
    client_for: ClientFor, company_ta: User, jd_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache.clear()
    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"jd_parse": "2/hour"})
    client = client_for(company_ta)

    codes = [
        client.post(PARSES, {"file": jd_upload()}, format="multipart").status_code for _ in range(3)
    ]

    assert codes == [202, 202, 429]
    cache.clear()
