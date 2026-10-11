from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from apps.hiring.models import InterviewFormat
from apps.hiring.tests.factories import ApprovalRequestFactory, CandidateNoteFactory
from apps.hiring.workflow import archive, move_forward, schedule_interview

pytestmark = pytest.mark.django_db


def read(client_for: Any, recruiter_user: Any, application: Any) -> Any:
    response = client_for(recruiter_user).get(f"/api/v1/recruiters/me/submissions/{application.pk}")
    assert response.status_code == 200
    return response.json()


def test_recruiter_sees_progress_on_their_submission(
    client_for: Any, recruiter_user: Any, application: Any, company_ta: Any
) -> None:
    move_forward(application)
    start = datetime(2026, 9, 22, 2, 0, tzinfo=UTC)
    schedule_interview(
        application,
        created_by=company_ta,
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        format=InterviewFormat.ONLINE,
        location="https://meet.google.com/abc-defg-hij",
    )
    body = read(client_for, recruiter_user, application)
    assert body["candidate"]["id"] == str(application.candidate_id)
    assert body["candidate"]["languages"] == ["Vietnamese", "English"]
    assert [entry["stage"] for entry in body["history"]] == ["new", "screen", "interview"]
    assert body["interviews"][0]["round"] == 1
    assert body["cv_kind"] == "pdf"
    assert body["rejected_reason"] == ""
    assert body["consultant_note"] == "Strong on Go and Kafka."


def test_recruiter_sees_why_the_candidate_was_archived(
    client_for: Any, recruiter_user: Any, application: Any
) -> None:
    archive(application, "Took another offer")
    body = read(client_for, recruiter_user, application)
    assert (body["stage"], body["rejected_reason"]) == ("rejected", "Took another offer")


def test_recruiter_detail_has_no_company_only_fields(
    client_for: Any, recruiter_user: Any, application: Any, company_ta: Any, company_admin: Any
) -> None:
    CandidateNoteFactory(application=application, author=company_ta)
    ApprovalRequestFactory(application=application, requested_by=company_ta, approver=company_admin)
    body = read(client_for, recruiter_user, application)
    assert not {"notes", "company_notes", "feedback", "pending_approval", "actions"} & set(body)
