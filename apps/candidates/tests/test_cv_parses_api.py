from __future__ import annotations

from collections.abc import Callable

import pytest
from django.core.cache import cache
from django.core.files.base import ContentFile
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.models import User
from apps.candidates.models import CvParse, CvParseStatus
from apps.candidates.tests.factories import CvParseFactory
from apps.core.testing.pdf import make_pdf
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db

PARSES = "/api/v1/cv-parses"

ClientFor = Callable[[User], APIClient]


def cv_upload(name: str = "Truong-My-Hanh_CV_2026.pdf") -> ContentFile[bytes]:
    return ContentFile(make_pdf("Truong My Hanh\nPayments Backend Lead"), name=name)


def test_uploading_a_cv_returns_202_and_a_queued_row(
    client_for: ClientFor, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    response = client_for(recruiter_user).post(PARSES, {"file": cv_upload()}, format="multipart")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["fields"] == {}
    parse = CvParse.objects.get(pk=body["id"])
    assert parse.owner == recruiter_profile
    assert parse.filename == "Truong-My-Hanh_CV_2026.pdf"


def test_a_renamed_binary_is_rejected(client_for: ClientFor, recruiter_user: User) -> None:
    response = client_for(recruiter_user).post(
        PARSES, {"file": ContentFile(b"MZ not a pdf", name="virus.pdf")}, format="multipart"
    )

    assert response.status_code == 400
    assert "does not look like" in str(response.json()["file"])
    assert CvParse.objects.count() == 0


def test_an_image_is_rejected(client_for: ClientFor, recruiter_user: User) -> None:
    response = client_for(recruiter_user).post(
        PARSES, {"file": ContentFile(b"\x89PNG\r\n\x1a\n", name="cv.png")}, format="multipart"
    )

    assert response.status_code == 400
    assert "PDF, DOCX or DOC" in str(response.json()["file"])


def test_a_request_without_a_file_is_rejected(client_for: ClientFor, recruiter_user: User) -> None:
    response = client_for(recruiter_user).post(PARSES, {}, format="multipart")

    assert response.status_code == 400


def test_the_owner_can_poll_their_parse(
    client_for: ClientFor, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    parse = CvParseFactory(
        owner=recruiter_profile,
        status=CvParseStatus.DONE,
        fields={
            "full_name": "Truong My Hanh",
            "current_title": "Payments Backend Lead",
            "email": "myhanh.truong@gmail.com",
            "phone": "+84909172846",
            "skills": ["Go"],
            "years_experience": 9,
        },
    )

    body = client_for(recruiter_user).get(f"{PARSES}/{parse.pk}").json()

    assert body["status"] == "done"
    assert body["fields"]["full_name"] == "Truong My Hanh"
    assert body["error"] == ""


def test_a_failed_parse_reports_its_error(
    client_for: ClientFor, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    parse = CvParseFactory(
        owner=recruiter_profile,
        status=CvParseStatus.FAILED,
        error="We couldn't read that CV. Fill the form in yourself.",
    )

    body = client_for(recruiter_user).get(f"{PARSES}/{parse.pk}").json()

    assert body["status"] == "failed"
    assert body["error"] == "We couldn't read that CV. Fill the form in yourself."


def test_another_recruiters_parse_is_404(client_for: ClientFor, recruiter_user: User) -> None:
    someone_elses = CvParseFactory()

    assert client_for(recruiter_user).get(f"{PARSES}/{someone_elses.pk}").status_code == 404


def test_a_company_user_cannot_upload(client_for: ClientFor, company_ta: User) -> None:
    response = client_for(company_ta).post(PARSES, {"file": cv_upload()}, format="multipart")

    assert response.status_code == 403
    assert response.json()["detail"] == "This area is for recruiter accounts."


def test_a_pending_recruiter_is_blocked(
    client_for: ClientFor, pending_recruiter_user: User
) -> None:
    response = client_for(pending_recruiter_user).post(
        PARSES, {"file": cv_upload()}, format="multipart"
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Your recruiter profile is waiting for review."


def test_anonymous_is_rejected(api_client: APIClient) -> None:
    assert api_client.post(PARSES, {"file": cv_upload()}, format="multipart").status_code == 403


def test_cv_parse_uploads_are_rate_limited(
    client_for: ClientFor, recruiter_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache.clear()
    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"cv_parse": "2/hour"})
    client = client_for(recruiter_user)

    codes = [
        client.post(PARSES, {"file": cv_upload()}, format="multipart").status_code for _ in range(3)
    ]

    assert codes == [202, 202, 429]
    cache.clear()
