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
from apps.jobs.models import JobAssignment, JobStatus
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


NOT_ASSIGNED = "You are no longer assigned to this job."
NOT_OPEN = "This job is no longer open for submissions."
NOT_JOINED = "Join this job from the marketplace before you submit a candidate."


def submission_urls(job: Any) -> tuple[str, str]:
    return f"/api/v1/jobs/{job.pk}/submissions/check", f"/api/v1/jobs/{job.pk}/submissions"


def post_both(client: Any, job: Any, candidate: Any) -> tuple[Any, Any]:
    check, submit = submission_urls(job)
    body = {"candidate_id": str(candidate.pk)}
    return (
        client.post(check, body, format="json"),
        client.post(submit, {**body, **NOTES, "cv_file": upload()}, format="multipart"),
    )


def test_submission_permissions(
    recruiter_client: Any, recruiter_profile: Any, joined_job: Any
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile)
    other = CandidateFactory()
    check = f"/api/v1/jobs/{joined_job.id}/submissions/check"
    # Someone else's candidate is not theirs to submit.
    assert (
        recruiter_client.post(check, {"candidate_id": str(other.pk)}, format="json").status_code
        == 404
    )
    # An assigned, live job takes the submission.
    assert (
        recruiter_client.post(check, {"candidate_id": str(candidate.pk)}, format="json").status_code
        == 200
    )
    # Assigned but no longer live: they can see it, so 403 NOT_OPEN.
    joined_job.status = JobStatus.CLOSED
    joined_job.save(update_fields=["status"])
    refused = recruiter_client.post(check, {"candidate_id": str(candidate.pk)}, format="json")
    assert refused.status_code == 403
    assert refused.json() == {"detail": NOT_OPEN}


def test_removed_recruiter_cannot_submit(
    recruiter_client: Any, recruiter_profile: Any, joined_job: Any
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile)
    ApplicationFactory(
        job=joined_job,
        recruiter=recruiter_profile,
        candidate=CandidateFactory(owner=recruiter_profile),
    )
    JobAssignment.objects.filter(job=joined_job, recruiter=recruiter_profile).update(
        left_at=timezone.now()
    )

    checked, submitted = post_both(recruiter_client, joined_job, candidate)

    assert (checked.status_code, submitted.status_code) == (403, 403)
    assert checked.json() == {"detail": NOT_ASSIGNED}
    assert submitted.json() == {"detail": NOT_ASSIGNED}
    assert Application.objects.filter(job=joined_job).count() == 1


def test_removed_recruiter_on_a_closed_job_is_told_they_are_not_assigned(
    recruiter_client: Any, recruiter_profile: Any, joined_job: Any
) -> None:
    ApplicationFactory(
        job=joined_job,
        recruiter=recruiter_profile,
        candidate=CandidateFactory(owner=recruiter_profile),
    )
    JobAssignment.objects.filter(job=joined_job, recruiter=recruiter_profile).update(
        left_at=timezone.now()
    )
    joined_job.status = JobStatus.CLOSED
    joined_job.save(update_fields=["status"])
    candidate = CandidateFactory(owner=recruiter_profile)

    checked, _ = post_both(recruiter_client, joined_job, candidate)

    assert (checked.status_code, checked.json()) == (403, {"detail": NOT_ASSIGNED})


def test_unassigned_job_submit_is_404(
    recruiter_client: Any, recruiter_profile: Any, company: Any
) -> None:
    live_job = JobFactory(company=company)
    candidate = CandidateFactory(owner=recruiter_profile)

    checked, submitted = post_both(recruiter_client, live_job, candidate)

    assert (checked.status_code, submitted.status_code) == (404, 404)
    assert not Application.objects.filter(job=live_job).exists()


def test_removed_recruiter_without_a_cv_gets_404(
    recruiter_client: Any, recruiter_profile: Any, joined_job: Any
) -> None:
    JobAssignment.objects.filter(job=joined_job, recruiter=recruiter_profile).update(
        left_at=timezone.now()
    )
    candidate = CandidateFactory(owner=recruiter_profile)

    checked, submitted = post_both(recruiter_client, joined_job, candidate)

    assert (checked.status_code, submitted.status_code) == (404, 404)


def test_marketplace_job_not_yet_joined_is_403_only_with_the_marketplace_on(
    recruiter_client: Any, recruiter_profile: Any, company: Any, settings: Any
) -> None:
    listed = JobFactory(company=company, on_marketplace=True)
    candidate = CandidateFactory(owner=recruiter_profile)

    settings.MARKETPLACE_ENABLED = False
    assert post_both(recruiter_client, listed, candidate)[0].status_code == 404

    settings.MARKETPLACE_ENABLED = True
    checked, submitted = post_both(recruiter_client, listed, candidate)
    assert (checked.status_code, submitted.status_code) == (403, 403)
    assert checked.json() == {"detail": NOT_JOINED}
    assert not Application.objects.filter(job=listed).exists()


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


def test_recruiter_reads_own_submission_with_notes(
    client_for: Any, recruiter_user: Any, recruiter_profile: Any, joined_job: Any
) -> None:
    mine = ApplicationFactory(
        job=joined_job,
        recruiter=recruiter_profile,
        candidate=CandidateFactory(owner=recruiter_profile),
        motivation="Larger projects",
        consultant_note="Sharp in the screen",
    )
    response = client_for(recruiter_user).get(f"/api/v1/recruiters/me/submissions/{mine.pk}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(mine.pk)
    assert body["job"]["id"] == str(joined_job.pk)
    assert body["motivation"] == "Larger projects"
    assert body["consultant_note"] == "Sharp in the screen"
    assert body["rejected_reason"] == ""


def test_recruiter_cannot_read_a_rival_submission(
    client_for: Any, recruiter_user: Any, joined_job: Any
) -> None:
    rival = RecruiterProfileFactory(nickname="Bluefin Search")
    theirs = ApplicationFactory(
        job=joined_job, recruiter=rival, candidate=CandidateFactory(owner=rival)
    )
    client = client_for(recruiter_user)
    assert client.get(f"/api/v1/recruiters/me/submissions/{theirs.pk}").status_code == 404
    assert client.get(f"/api/v1/recruiters/me/submissions/{theirs.pk}/cv").status_code == 404


def test_recruiter_downloads_the_cv_they_submitted(
    client_for: Any, recruiter_user: Any, recruiter_profile: Any, joined_job: Any
) -> None:
    mine = ApplicationFactory(
        job=joined_job,
        recruiter=recruiter_profile,
        candidate=CandidateFactory(owner=recruiter_profile),
        cv_filename="Nguyễn Văn A.pdf",
    )
    response = client_for(recruiter_user).get(f"/api/v1/recruiters/me/submissions/{mine.pk}/cv")
    assert response.status_code == 200
    assert b"%PDF" in b"".join(response.streaming_content)[:8]
    assert "filename*=UTF-8''" in response["Content-Disposition"]

    mine.cv_file.storage.delete(mine.cv_file.name)
    response = client_for(recruiter_user).get(f"/api/v1/recruiters/me/submissions/{mine.pk}/cv")
    assert response.status_code == 404
