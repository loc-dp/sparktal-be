from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import timedelta
from typing import Any

import pytest
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.models import User
from apps.candidates.models import CvParse, CvParseReader, CvParseStatus
from apps.candidates.tests.factories import CvParseFactory
from apps.core.testing.pdf import make_pdf
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

PARSES = "/api/v1/cv-parses"

ClientFor = Callable[[User], APIClient]
CaptureCommits = Callable[..., AbstractContextManager[list[Callable[[], Any]]]]


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


def a_done_claude_read(owner: RecruiterProfile, upload: ContentFile[bytes]) -> CvParse:
    """The parse a first upload of `upload` leaves behind once Claude has read it."""
    return CvParseFactory.create(
        owner=owner,
        status=CvParseStatus.DONE,
        reader=CvParseReader.CLAUDE,
        file_sha256=hashlib.sha256(upload.read()).hexdigest(),
        fields={"full_name": "Truong My Hanh", "current_title": "Payments Backend Lead"},
    )


def test_re_uploading_a_file_reuses_the_earlier_read(
    client_for: ClientFor,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
    django_capture_on_commit_callbacks: CaptureCommits,
) -> None:
    earlier = a_done_claude_read(recruiter_profile, cv_upload())

    with django_capture_on_commit_callbacks() as callbacks:
        response = client_for(recruiter_user).post(
            PARSES, {"file": cv_upload("copy.pdf")}, format="multipart"
        )

    assert response.status_code == 202
    body = response.json()
    assert body["id"] != str(earlier.pk)
    assert body["status"] == "done"
    assert body["fields"] == earlier.fields
    assert body["filename"] == "copy.pdf"
    copy = CvParse.objects.get(pk=body["id"])
    assert copy.reused
    assert not copy.file
    assert copy.input_tokens == 0
    assert callbacks == []


def test_another_recruiters_read_of_the_same_file_is_not_reused(
    client_for: ClientFor, recruiter_user: User
) -> None:
    a_done_claude_read(RecruiterProfileFactory(), cv_upload())

    body = client_for(recruiter_user).post(PARSES, {"file": cv_upload()}, format="multipart").json()

    assert body["status"] == "queued"
    assert not CvParse.objects.get(pk=body["id"]).reused


@pytest.mark.parametrize(
    "change",
    [
        {"reader": CvParseReader.RULES},
        {"status": CvParseStatus.FAILED},
        {"status": CvParseStatus.RUNNING},
        {"created_at": timezone.now() - timedelta(hours=25)},
    ],
    ids=["rules-only read", "failed", "still running", "older than a day"],
)
def test_a_read_that_is_not_a_finished_claude_read_from_today_is_not_reused(
    client_for: ClientFor,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
    change: dict[str, Any],
) -> None:
    earlier = a_done_claude_read(recruiter_profile, cv_upload())
    CvParse.objects.filter(pk=earlier.pk).update(**change)

    body = client_for(recruiter_user).post(PARSES, {"file": cv_upload()}, format="multipart").json()

    assert body["status"] == "queued"


def test_a_recruiter_past_the_daily_limit_gets_429(
    client_for: ClientFor,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
    settings: Any,
) -> None:
    settings.CV_PARSE_DAILY_LIMIT = 2
    CvParseFactory.create_batch(2, owner=recruiter_profile)

    response = client_for(recruiter_user).post(PARSES, {"file": cv_upload()}, format="multipart")

    assert response.status_code == 429
    assert response.json()["detail"] == (
        "You've hit today's CV reading limit. Fill the form in yourself, or try again later."
    )
    assert CvParse.objects.count() == 2


def test_the_daily_limit_skips_reused_and_day_old_parses(
    client_for: ClientFor,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
    settings: Any,
) -> None:
    settings.CV_PARSE_DAILY_LIMIT = 2
    CvParseFactory(owner=recruiter_profile)
    CvParseFactory(owner=recruiter_profile, reused=True)
    old = CvParseFactory(owner=recruiter_profile)
    CvParse.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=25))
    CvParseFactory.create_batch(3)

    response = client_for(recruiter_user).post(PARSES, {"file": cv_upload()}, format="multipart")

    assert response.status_code == 202


def test_a_re_upload_is_served_even_past_the_daily_limit(
    client_for: ClientFor,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
    settings: Any,
) -> None:
    settings.CV_PARSE_DAILY_LIMIT = 1
    a_done_claude_read(recruiter_profile, cv_upload())

    body = client_for(recruiter_user).post(PARSES, {"file": cv_upload()}, format="multipart").json()

    assert body["status"] == "done"
