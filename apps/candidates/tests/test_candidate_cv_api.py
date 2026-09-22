from __future__ import annotations

from collections.abc import Callable

import pytest
from django.core.files.base import ContentFile
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.tests.factories import CandidateFactory
from apps.core.testing.pdf import make_pdf
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db

CANDIDATES = "/api/v1/candidates"

ClientFor = Callable[[User], APIClient]


def cv(name: str = "Truong-My-Hanh_CV_2026.pdf") -> ContentFile[bytes]:
    return ContentFile(make_pdf("Truong My Hanh"), name=name)


def test_uploading_a_cv_stores_the_original_filename(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile)

    response = recruiter_client.put(
        f"{CANDIDATES}/{candidate.pk}/cv", {"file": cv()}, format="multipart"
    )

    assert response.status_code == 200
    assert response.json()["cv_filename"] == "Truong-My-Hanh_CV_2026.pdf"
    assert response.json()["has_cv"] is True
    candidate.refresh_from_db()
    assert candidate.cv_file.read().startswith(b"%PDF-")


def test_uploading_again_replaces_the_previous_cv(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile)
    recruiter_client.put(f"{CANDIDATES}/{candidate.pk}/cv", {"file": cv()}, format="multipart")

    recruiter_client.put(
        f"{CANDIDATES}/{candidate.pk}/cv", {"file": cv("new.pdf")}, format="multipart"
    )

    candidate.refresh_from_db()
    assert candidate.cv_filename == "new.pdf"


def test_a_renamed_binary_is_rejected(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile)

    response = recruiter_client.put(
        f"{CANDIDATES}/{candidate.pk}/cv",
        {"file": ContentFile(b"MZ not a pdf", name="virus.pdf")},
        format="multipart",
    )

    assert response.status_code == 400
    assert "does not look like" in str(response.json()["file"])
    candidate.refresh_from_db()
    assert not candidate.cv_file


def test_json_is_not_accepted_on_the_cv_endpoint(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile)

    response = recruiter_client.put(
        f"{CANDIDATES}/{candidate.pk}/cv", {"file": "not a file"}, format="json"
    )

    assert response.status_code == 415


def test_downloading_streams_the_cv_as_an_attachment(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile)
    recruiter_client.put(f"{CANDIDATES}/{candidate.pk}/cv", {"file": cv()}, format="multipart")

    response = recruiter_client.get(f"{CANDIDATES}/{candidate.pk}/cv")

    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")
    assert "Truong-My-Hanh_CV_2026.pdf" in response["Content-Disposition"]
    assert response["X-Content-Type-Options"] == "nosniff"
    assert b"".join(response.streaming_content).startswith(b"%PDF-")


def test_a_candidate_without_a_cv_is_404(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile)

    assert recruiter_client.get(f"{CANDIDATES}/{candidate.pk}/cv").status_code == 404


def test_another_recruiters_cv_is_404(recruiter_client: APIClient) -> None:
    someone_elses = CandidateFactory()
    someone_elses.cv_file.save("cv.pdf", cv(), save=True)

    assert recruiter_client.get(f"{CANDIDATES}/{someone_elses.pk}/cv").status_code == 404
    assert (
        recruiter_client.put(
            f"{CANDIDATES}/{someone_elses.pk}/cv", {"file": cv()}, format="multipart"
        ).status_code
        == 404
    )


def test_a_company_user_cannot_touch_a_pool_cv(client_for: ClientFor, company_ta: User) -> None:
    candidate = CandidateFactory()

    assert client_for(company_ta).get(f"{CANDIDATES}/{candidate.pk}/cv").status_code == 403
