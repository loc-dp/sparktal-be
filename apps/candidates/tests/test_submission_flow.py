from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.candidates.duplicates import find_duplicate, window_start
from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.chat.models import Channel, MessageKind
from apps.companies.tests.factories import CompanyFactory
from apps.core.testing.pdf import make_pdf
from apps.jobs.models import JobStatus
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

NOTES = {"reason_for_leaving": "Limited growth", "motivation": "Larger projects"}


def upload() -> ContentFile[bytes]:
    return ContentFile(make_pdf("Candidate CV"), name="candidate.pdf")


def test_calendar_window_and_company_scope(company: Any) -> None:
    assert (
        window_start(
            1, now=timezone.datetime(2026, 3, 31, tzinfo=timezone.get_current_timezone())
        ).day
        == 28
    )
    first = ApplicationFactory(
        job=JobFactory(company=company),
        candidate=CandidateFactory(email="same@example.com"),
        submitted_at=timezone.now() - timedelta(days=40),
    )
    newcomer = CandidateFactory(email="same@example.com")
    assert find_duplicate(company, newcomer) == first
    assert find_duplicate(CompanyFactory(), newcomer) is None
    assert find_duplicate(company, newcomer, within_months=1) is None


def test_empty_hashes_never_collide(company: Any) -> None:
    ApplicationFactory(
        job=JobFactory(company=company), candidate=CandidateFactory(email="", phone="", dob=None)
    )
    assert find_duplicate(company, CandidateFactory(email="", phone="", dob=None)) is None


def test_submission_and_anonymous_duplicate_check(
    recruiter_client: Any, recruiter_profile: Any, joined_job: Any
) -> None:
    rival = RecruiterProfileFactory(nickname="Bluefin Search", user__email="bluefin@example.com")
    earlier = ApplicationFactory(
        job=JobFactory(company=joined_job.company),
        recruiter=rival,
        candidate=CandidateFactory(owner=rival, email="same@example.com"),
    )
    candidate = CandidateFactory(owner=recruiter_profile, email="same@example.com")
    check = recruiter_client.post(
        f"/api/v1/jobs/{joined_job.id}/submissions/check",
        {"candidate_id": str(candidate.pk)},
        format="json",
    )
    assert check.status_code == 200
    assert check.json()["duplicate"] is True
    assert set(check.json()) == {"duplicate", "first_submitted_at", "window_months"}
    assert "Bluefin" not in check.content.decode()
    submitted = recruiter_client.post(
        f"/api/v1/jobs/{joined_job.id}/submissions",
        {
            "candidate_id": str(candidate.pk),
            **NOTES,
            "cv_file": upload(),
            "stage": Stage.OFFER,
            "is_duplicate": "false",
        },
        format="multipart",
    )
    assert submitted.status_code == 201, submitted.content
    application = Application.objects.get(pk=submitted.json()["application_id"])
    assert application.duplicate_of == earlier
    assert application.is_duplicate is True
    assert application.stage == Stage.NEW
    assert application.motivation == NOTES["motivation"]
    assert Channel.objects.filter(job=joined_job, recruiter=recruiter_profile).count() == 1
    assert (
        Channel.objects.get(job=joined_job, recruiter=recruiter_profile)
        .messages.filter(kind=MessageKind.SYSTEM)
        .count()
        == 2
    )
    again = recruiter_client.post(
        f"/api/v1/jobs/{joined_job.id}/submissions",
        {"candidate_id": str(candidate.pk), **NOTES, "cv_file": upload()},
        format="multipart",
    )
    assert again.status_code == 409


def test_submission_requires_cv_and_mandatory_notes(
    recruiter_client: Any, recruiter_profile: Any, joined_job: Any
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile)
    url = f"/api/v1/jobs/{joined_job.id}/submissions"
    assert (
        recruiter_client.post(
            url, {"candidate_id": str(candidate.pk), **NOTES}, format="multipart"
        ).status_code
        == 400
    )
    for missing in NOTES:
        payload = {"candidate_id": str(candidate.pk), **NOTES, "cv_file": upload()}
        payload[missing] = "  "
        assert missing in recruiter_client.post(url, payload, format="multipart").json()
    bad = recruiter_client.post(
        url,
        {
            "candidate_id": str(candidate.pk),
            **NOTES,
            "cv_file": ContentFile(b"MZ", name="fake.pdf"),
        },
        format="multipart",
    )
    assert bad.status_code == 400


def test_submission_copies_pool_cv(
    recruiter_client: Any, recruiter_profile: Any, joined_job: Any
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile, cv_filename="original.pdf")
    candidate.cv_file.save("original.pdf", ContentFile(make_pdf("Pool CV")), save=True)
    response = recruiter_client.post(
        f"/api/v1/jobs/{joined_job.id}/submissions",
        {"candidate_id": str(candidate.pk), **NOTES},
        format="multipart",
    )
    assert response.status_code == 201, response.content
    application = Application.objects.get(pk=response.json()["application_id"])
    assert application.cv_filename == "original.pdf"
    assert application.cv_file.name != candidate.cv_file.name


def test_submission_permissions(
    recruiter_client: Any, recruiter_profile: Any, joined_job: Any
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile)
    other = CandidateFactory()
    check = f"/api/v1/jobs/{joined_job.id}/submissions/check"
    assert (
        recruiter_client.post(check, {"candidate_id": str(other.pk)}, format="json").status_code
        == 404
    )
    unjoined = JobFactory(company=joined_job.company)
    assert (
        recruiter_client.post(
            f"/api/v1/jobs/{unjoined.pk}/submissions/check",
            {"candidate_id": str(candidate.pk)},
            format="json",
        ).status_code
        == 403
    )
    joined_job.status = JobStatus.CLOSED
    joined_job.save(update_fields=["status"])
    assert (
        recruiter_client.post(check, {"candidate_id": str(candidate.pk)}, format="json").status_code
        == 403
    )


def test_recruiter_list_is_private_and_filterable(
    client_for: Any, recruiter_user: Any, recruiter_profile: Any, joined_job: Any
) -> None:
    mine = ApplicationFactory(
        job=joined_job,
        recruiter=recruiter_profile,
        candidate=CandidateFactory(owner=recruiter_profile),
    )
    rival = RecruiterProfileFactory(nickname="Bluefin Search")
    ApplicationFactory(job=joined_job, recruiter=rival, candidate=CandidateFactory(owner=rival))
    url = f"/api/v1/recruiters/me/submissions?job={joined_job.pk}&stage=new"
    response = client_for(recruiter_user).get(url)
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["results"]] == [str(mine.pk)]
    assert "Bluefin" not in response.content.decode()
    assert (
        client_for(recruiter_user).get("/api/v1/recruiters/me/submissions?stage=nope").status_code
        == 400
    )
