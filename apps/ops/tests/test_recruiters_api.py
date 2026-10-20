"""Sparktal ops: the recruiter review queue, and approving or rejecting one."""

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User, UserKind
from apps.accounts.tests.factories import UserFactory
from apps.jobs.models import JobStatus
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory
from apps.recruiters.models import RecruiterProfile, RecruiterStatus
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

Commit = Callable[..., Any]

RECRUITERS = "/api/v1/ops/recruiters"
ALREADY_APPROVED = "This recruiter is already approved."
ALREADY_REJECTED = "This recruiter is already rejected."
TELL_THEM_WHY = "Tell them why."

ROW_KEYS = {
    "id",
    "nickname",
    "full_name",
    "email",
    "status",
    "expertise",
    "years_experience",
    "linkedin",
    "bio",
    "submitted_at",
    "reviewed_at",
    "review_note",
    "active_jobs",
}


def make_recruiter(
    status: str = RecruiterStatus.PENDING, *, submitted_days_ago: int | None = 1, **fields: Any
) -> RecruiterProfile:
    submitted = (
        timezone.now() - timedelta(days=submitted_days_ago)
        if submitted_days_ago is not None
        else None
    )
    return cast(
        RecruiterProfile,
        RecruiterProfileFactory(status=status, submitted_at=submitted, **fields),
    )


def recruiter_url(recruiter: RecruiterProfile, action: str) -> str:
    return f"{RECRUITERS}/{recruiter.pk}/{action}"


def approve(client: APIClient, recruiter: RecruiterProfile) -> Any:
    return client.post(recruiter_url(recruiter, "approve"))


def reject(client: APIClient, recruiter: RecruiterProfile, **body: Any) -> Any:
    return client.post(recruiter_url(recruiter, "reject"), body, format="json")


def listed(client: APIClient, **params: str) -> list[dict[str, Any]]:
    response = client.get(RECRUITERS, params)
    assert response.status_code == 200
    return cast(list[dict[str, Any]], response.json()["results"])


# The queue ------------------------------------------------------------------------------------


def test_pending_queue(staff_client: APIClient) -> None:
    newest = make_recruiter(submitted_days_ago=1, nickname="Newest")
    oldest = make_recruiter(
        submitted_days_ago=5,
        nickname="Oldest",
        user=UserFactory(
            kind=UserKind.RECRUITER, email="mai@example.com", full_name="Nguyễn Thị Mai"
        ),
        expertise=["backend", "data"],
        years_experience=7,
        linkedin="https://linkedin.com/in/mai",
        bio="Eight years placing engineers.",
    )
    make_recruiter(RecruiterStatus.APPROVED, nickname="Already in")
    make_recruiter(RecruiterStatus.REJECTED, nickname="Turned away")

    response = staff_client.get(RECRUITERS, {"status": "pending"})

    assert response.status_code == 200
    body = response.json()
    assert {"count", "next", "previous", "results"} <= set(body)
    assert body["count"] == 2
    # The queue is oldest first.
    assert [row["id"] for row in body["results"]] == [str(oldest.pk), str(newest.pk)]
    row = body["results"][0]
    assert set(row) == ROW_KEYS
    assert row["nickname"] == "Oldest"
    assert row["full_name"] == "Nguyễn Thị Mai"
    assert row["email"] == "mai@example.com"
    assert row["status"] == "pending"
    assert row["expertise"] == ["backend", "data"]
    assert row["years_experience"] == 7
    assert row["linkedin"] == "https://linkedin.com/in/mai"
    assert row["bio"] == "Eight years placing engineers."
    assert row["submitted_at"] is not None
    assert row["reviewed_at"] is None
    assert row["review_note"] == ""
    assert row["active_jobs"] == 0


def test_status_filter_and_the_unfiltered_list(staff_client: APIClient) -> None:
    pending = make_recruiter(nickname="Bình")
    approved = make_recruiter(RecruiterStatus.APPROVED, nickname="An")
    rejected = make_recruiter(RecruiterStatus.REJECTED, nickname="Cường")

    assert [row["id"] for row in listed(staff_client, status="approved")] == [str(approved.pk)]
    assert [row["id"] for row in listed(staff_client, status="rejected")] == [str(rejected.pk)]
    assert [row["id"] for row in listed(staff_client, status="pending")] == [str(pending.pk)]
    # No status: everyone, by nickname (the invite picker reads `approved` this way).
    assert [row["id"] for row in listed(staff_client)] == [
        str(approved.pk),
        str(pending.pk),
        str(rejected.pk),
    ]


def test_unknown_status_is_400(staff_client: APIClient) -> None:
    assert staff_client.get(RECRUITERS, {"status": "banana"}).status_code == 400


def test_search_by_email(staff_client: APIClient) -> None:
    wanted = make_recruiter(user=UserFactory(kind=UserKind.RECRUITER, email="mai.tran@talent.vn"))
    make_recruiter(user=UserFactory(kind=UserKind.RECRUITER, email="other@example.com"))

    rows = listed(staff_client, search="TALENT.vn")

    assert [row["id"] for row in rows] == [str(wanted.pk)]


def test_search_by_nickname_and_full_name(staff_client: APIClient) -> None:
    by_nickname = make_recruiter(nickname="Falcon Hunter")
    by_name = make_recruiter(
        nickname="Quiet one",
        user=UserFactory(kind=UserKind.RECRUITER, full_name="Lê Văn Falcone"),
    )
    make_recruiter(nickname="Someone else")

    rows = listed(staff_client, search="falcon")

    assert {row["id"] for row in rows} == {str(by_nickname.pk), str(by_name.pk)}
    # A search of blanks is no search.
    assert len(listed(staff_client, search="  ")) == 3


def test_search_and_status_combine(staff_client: APIClient) -> None:
    make_recruiter(RecruiterStatus.APPROVED, nickname="Anna approved")
    pending = make_recruiter(nickname="Anna pending")

    rows = listed(staff_client, status="pending", search="anna")

    assert [row["id"] for row in rows] == [str(pending.pk)]


def test_queue_is_paginated(staff_client: APIClient) -> None:
    for _ in range(3):
        make_recruiter()

    response = staff_client.get(RECRUITERS, {"page_size": 2})

    body = response.json()
    assert body["count"] == 3
    assert len(body["results"]) == 2
    assert body["next"] is not None


def test_active_jobs_counts_the_jobs_the_recruiter_works_now(staff_client: APIClient) -> None:
    recruiter = make_recruiter(RecruiterStatus.APPROVED)
    # Counted: a live job they are still on.
    JobAssignmentFactory(recruiter=recruiter, job=JobFactory(status=JobStatus.ACTIVE))
    JobAssignmentFactory(recruiter=recruiter, job=JobFactory(status=JobStatus.ACTIVE))
    # Not counted: they left, the job was deleted, or the job is not live.
    JobAssignmentFactory(
        recruiter=recruiter, job=JobFactory(status=JobStatus.ACTIVE), left_at=timezone.now()
    )
    JobAssignmentFactory(
        recruiter=recruiter, job=JobFactory(status=JobStatus.ACTIVE, deleted_at=timezone.now())
    )
    JobAssignmentFactory(recruiter=recruiter, job=JobFactory(status=JobStatus.CLOSED))
    JobAssignmentFactory(recruiter=recruiter, job=JobFactory(status=JobStatus.PENDING))
    # Someone else's job does not count for this recruiter.
    other = make_recruiter(RecruiterStatus.APPROVED)
    JobAssignmentFactory(recruiter=other, job=JobFactory(status=JobStatus.ACTIVE))

    counts = {row["id"]: row["active_jobs"] for row in listed(staff_client)}

    assert counts == {str(recruiter.pk): 2, str(other.pk): 1}


def test_list_query_count_does_not_grow(
    staff_client: APIClient, django_assert_max_num_queries: Any
) -> None:
    for _ in range(8):
        recruiter = make_recruiter(RecruiterStatus.APPROVED)
        JobAssignmentFactory(recruiter=recruiter, job=JobFactory(status=JobStatus.ACTIVE))

    # Session, user, count, page: the same however many recruiters there are.
    with django_assert_max_num_queries(5):
        response = staff_client.get(RECRUITERS)

    assert len(response.json()["results"]) == 8


# Approve --------------------------------------------------------------------------------------


def test_approve_sends_email(
    staff_client: APIClient,
    staff_user: User,
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Commit,
) -> None:
    recruiter = make_recruiter(nickname="Mai", review_note="Old note")

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = approve(staff_client, recruiter)
        # The mail waits for the commit.
        assert mailoutbox == []

    assert response.status_code == 200
    assert len(callbacks) == 1
    body = response.json()
    assert set(body) == ROW_KEYS
    assert body["id"] == str(recruiter.pk)
    assert body["status"] == "approved"
    assert body["reviewed_at"] is not None
    assert body["review_note"] == ""
    recruiter.refresh_from_db()
    assert recruiter.status == RecruiterStatus.APPROVED
    assert recruiter.reviewed_by == staff_user
    assert len(mailoutbox) == 1
    message = mailoutbox[0]
    assert message.to == [recruiter.user.email]
    assert "approved" in message.subject.lower()
    assert "Mai" in message.body
    assert "/r/chat" in message.body


def test_approving_a_rejected_recruiter_is_allowed(
    staff_client: APIClient, django_capture_on_commit_callbacks: Commit
) -> None:
    recruiter = make_recruiter(RecruiterStatus.REJECTED, review_note="Too thin.")

    with django_capture_on_commit_callbacks(execute=True):
        response = approve(staff_client, recruiter)

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["review_note"] == ""


def test_approve_twice_is_400(
    staff_client: APIClient, mailoutbox: list[Any], django_capture_on_commit_callbacks: Commit
) -> None:
    recruiter = make_recruiter()
    with django_capture_on_commit_callbacks(execute=True):
        assert approve(staff_client, recruiter).status_code == 200
    recruiter.refresh_from_db()
    reviewed_at = recruiter.reviewed_at

    with django_capture_on_commit_callbacks(execute=True):
        again = approve(staff_client, recruiter)

    assert again.status_code == 400
    assert again.json()["detail"] == ALREADY_APPROVED
    recruiter.refresh_from_db()
    assert recruiter.reviewed_at == reviewed_at
    # Told once, not twice.
    assert len(mailoutbox) == 1


# Reject ---------------------------------------------------------------------------------------


def test_reject_emails_the_reason(
    staff_client: APIClient,
    staff_user: User,
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Commit,
) -> None:
    recruiter = make_recruiter()

    with django_capture_on_commit_callbacks(execute=True):
        response = reject(staff_client, recruiter, note="  No track record.  ")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == ROW_KEYS
    assert body["status"] == "rejected"
    assert body["review_note"] == "No track record."
    assert body["reviewed_at"] is not None
    recruiter.refresh_from_db()
    assert recruiter.status == RecruiterStatus.REJECTED
    assert recruiter.reviewed_by == staff_user
    assert [message.to for message in mailoutbox] == [[recruiter.user.email]]
    assert "No track record." in mailoutbox[0].body


@pytest.mark.parametrize(
    "body", [{"note": ""}, {"note": "   "}, {}], ids=["empty", "blank", "none"]
)
def test_reject_needs_note(
    staff_client: APIClient,
    body: dict[str, Any],
    mailoutbox: list[Any],
    django_capture_on_commit_callbacks: Commit,
) -> None:
    recruiter = make_recruiter()

    with django_capture_on_commit_callbacks(execute=True):
        response = reject(staff_client, recruiter, **body)

    assert response.status_code == 400
    assert response.json() == {"note": [TELL_THEM_WHY]}
    recruiter.refresh_from_db()
    assert recruiter.status == RecruiterStatus.PENDING
    assert recruiter.reviewed_at is None
    assert mailoutbox == []


def test_reject_twice_is_400(
    staff_client: APIClient, mailoutbox: list[Any], django_capture_on_commit_callbacks: Commit
) -> None:
    recruiter = make_recruiter()
    with django_capture_on_commit_callbacks(execute=True):
        assert reject(staff_client, recruiter, note="Not a fit.").status_code == 200

    with django_capture_on_commit_callbacks(execute=True):
        again = reject(staff_client, recruiter, note="Changed my mind.")

    assert again.status_code == 400
    assert again.json()["detail"] == ALREADY_REJECTED
    recruiter.refresh_from_db()
    assert recruiter.review_note == "Not a fit."
    assert len(mailoutbox) == 1


def test_rejecting_an_approved_recruiter_is_allowed(
    staff_client: APIClient, django_capture_on_commit_callbacks: Commit
) -> None:
    recruiter = make_recruiter(RecruiterStatus.APPROVED)

    with django_capture_on_commit_callbacks(execute=True):
        response = reject(staff_client, recruiter, note="Pulled after a complaint.")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


# The mail never breaks the review --------------------------------------------------------------


def test_review_mail_failure_does_not_undo_the_review(
    staff_client: APIClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: Commit,
) -> None:
    def boom(self: Any, *args: Any, **kwargs: Any) -> int:
        raise OSError("smtp down")

    monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", boom)
    recruiter = make_recruiter()

    with caplog.at_level(logging.ERROR), django_capture_on_commit_callbacks(execute=True):
        response = approve(staff_client, recruiter)

    assert response.status_code == 200
    recruiter.refresh_from_db()
    assert recruiter.status == RecruiterStatus.APPROVED
    assert "Could not send email" in caplog.text


# Who may, and what is not there ------------------------------------------------------------------


@pytest.mark.parametrize("who", ["anonymous", "company_admin", "recruiter_user"])
def test_recruiter_review_is_staff_only(
    who: str, api_client: APIClient, request: pytest.FixtureRequest
) -> None:
    recruiter = make_recruiter()
    if who != "anonymous":
        api_client.force_login(request.getfixturevalue(who))

    assert api_client.get(RECRUITERS).status_code == 403
    assert approve(api_client, recruiter).status_code == 403
    assert reject(api_client, recruiter, note="Nope.").status_code == 403
    recruiter.refresh_from_db()
    assert recruiter.status == RecruiterStatus.PENDING


def test_unknown_recruiter_is_404(staff_client: APIClient) -> None:
    ghost = RecruiterProfile(id=uuid4())

    assert approve(staff_client, ghost).status_code == 404
    assert reject(staff_client, ghost, note="Who?").status_code == 404
    # Not there beats a bad note.
    assert reject(staff_client, ghost, note="").status_code == 404
