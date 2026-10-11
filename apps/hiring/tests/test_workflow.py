from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.candidates.models import Stage
from apps.chat.models import MessageKind
from apps.hiring import discussion
from apps.hiring.models import ApprovalStatus, InterviewFormat
from apps.hiring.tests.conftest import at_stage
from apps.hiring.tests.factories import ApprovalRequestFactory, InterviewFactory
from apps.hiring.workflow import (
    StageConflict,
    archive,
    available_actions,
    decide_approval,
    make_offer,
    mark_hired,
    move_forward,
    request_approval,
    schedule_interview,
)

pytestmark = pytest.mark.django_db

START = datetime(2026, 9, 22, 2, 0, tzinfo=UTC)  # 09:00 in Ho Chi Minh City


def book(application: Any, user: Any, **overrides: Any) -> Any:
    fields = {
        "starts_at": START,
        "ends_at": START + timedelta(hours=1),
        "format": InterviewFormat.ONLINE,
        "location": "https://meet.google.com/abc-defg-hij",
        **overrides,
    }
    return schedule_interview(application, created_by=user, **fields)


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        (Stage.SHORTLISTED, ["move_forward", "request_approval", "archive"]),
        (Stage.NEW, ["move_forward", "request_approval", "archive"]),
        (Stage.SCREEN, ["set_interview", "request_approval", "archive"]),
        (Stage.INTERVIEW, ["set_interview", "offer", "archive"]),
        (Stage.OFFER, ["hire", "archive"]),
        (Stage.ONBOARDED, []),
        (Stage.REJECTED, []),
    ],
)
def test_actions_follow_the_stage(application: Any, stage: str, expected: list[str]) -> None:
    assert available_actions(at_stage(application, stage)) == expected


def test_interview_with_a_round_offers_next_interview(application: Any) -> None:
    InterviewFactory(application=at_stage(application, Stage.INTERVIEW), round=1)
    assert available_actions(application) == ["next_interview", "offer", "archive"]


def test_pending_approval_hides_request_approval(
    application: Any, company_ta: Any, company_admin: Any
) -> None:
    ApprovalRequestFactory(application=application, requested_by=company_ta, approver=company_admin)
    assert available_actions(application) == ["move_forward", "archive"]


def test_move_forward_goes_to_screen(application: Any) -> None:
    move_forward(application)
    application.refresh_from_db()
    assert application.stage == Stage.SCREEN


def test_move_forward_from_screen_is_a_conflict(application: Any) -> None:
    with pytest.raises(StageConflict):
        move_forward(at_stage(application, Stage.SCREEN))


def test_schedule_from_screen_creates_round_one_and_moves_to_interview(
    application: Any, company_ta: Any
) -> None:
    interview = book(at_stage(application, Stage.SCREEN), company_ta)
    application.refresh_from_db()
    assert (interview.round, application.stage) == (1, Stage.INTERVIEW)
    assert interview.created_by == company_ta


def test_schedule_twice_creates_two_rounds_not_two_round_ones(
    application: Any, company_ta: Any
) -> None:
    at_stage(application, Stage.SCREEN)
    book(application, company_ta)
    second = book(
        application,
        company_ta,
        starts_at=START + timedelta(days=3),
        ends_at=START + timedelta(days=3, hours=1),
    )
    assert second.round == 2
    assert list(application.interviews.values_list("round", flat=True)) == [1, 2]


def test_schedule_from_new_is_a_conflict(application: Any, company_ta: Any) -> None:
    with pytest.raises(StageConflict):
        book(application, company_ta)


def test_schedule_posts_a_system_message_about_the_candidate(
    application: Any, company_ta: Any
) -> None:
    book(at_stage(application, Stage.SCREEN), company_ta)
    message = application.messages.get(body__startswith="Interview round")
    assert message.kind == MessageKind.SYSTEM
    assert message.body == (
        "Interview round 1 · 22/09/2026 09:00–10:00 · Online · https://meet.google.com/abc-defg-hij"
    )
    assert (message.channel.job, message.channel.recruiter) == (
        application.job,
        application.recruiter,
    )


def test_offer_then_hire(application: Any) -> None:
    make_offer(at_stage(application, Stage.INTERVIEW))
    application.refresh_from_db()
    assert application.stage == Stage.OFFER
    mark_hired(application)
    application.refresh_from_db()
    assert application.stage == Stage.ONBOARDED


def test_offer_from_screen_is_a_conflict(application: Any) -> None:
    with pytest.raises(StageConflict):
        make_offer(at_stage(application, Stage.SCREEN))


def test_archive_stores_the_reason(application: Any) -> None:
    archive(application, "Salary expectations far above the band.")
    application.refresh_from_db()
    assert application.stage == Stage.REJECTED
    assert application.rejected_reason == "Salary expectations far above the band."


def test_archive_from_onboarded_is_a_conflict(application: Any) -> None:
    with pytest.raises(StageConflict):
        archive(at_stage(application, Stage.ONBOARDED), "Too late")


def test_stage_change_cancels_the_pending_approval(
    application: Any, company_ta: Any, company_admin: Any
) -> None:
    pending = ApprovalRequestFactory(
        application=application, requested_by=company_ta, approver=company_admin
    )
    move_forward(application)
    pending.refresh_from_db()
    assert pending.status == ApprovalStatus.CANCELLED
    assert pending.decided_at is not None


def test_request_approval_records_the_current_stage(
    application: Any, company_ta: Any, company_admin: Any
) -> None:
    approval = request_approval(
        at_stage(application, Stage.SCREEN),
        requested_by=company_ta,
        approver=company_admin,
        note="Skip to the panel?",
    )
    assert (approval.stage, approval.status, approval.note) == (
        Stage.SCREEN,
        ApprovalStatus.PENDING,
        "Skip to the panel?",
    )


def test_second_request_while_one_is_pending_is_a_conflict(
    application: Any, company_ta: Any, company_admin: Any
) -> None:
    request_approval(application, requested_by=company_ta, approver=company_admin, note="")
    with pytest.raises(StageConflict):
        request_approval(application, requested_by=company_ta, approver=company_admin, note="")


def test_request_approval_from_interview_is_a_conflict(
    application: Any, company_ta: Any, company_admin: Any
) -> None:
    with pytest.raises(StageConflict):
        request_approval(
            at_stage(application, Stage.INTERVIEW),
            requested_by=company_ta,
            approver=company_admin,
            note="",
        )


@pytest.mark.parametrize(
    ("stage", "next_stage"),
    [(Stage.NEW, Stage.SCREEN), (Stage.SHORTLISTED, Stage.SCREEN), (Stage.SCREEN, Stage.INTERVIEW)],
)
def test_approve_moves_one_stage_on(
    application: Any, company_ta: Any, company_admin: Any, stage: str, next_stage: str
) -> None:
    approval = request_approval(
        at_stage(application, stage), requested_by=company_ta, approver=company_admin, note=""
    )
    decide_approval(approval, approve=True, note="Go")
    application.refresh_from_db()
    approval.refresh_from_db()
    assert application.stage == next_stage
    assert (approval.status, approval.decision_note) == (ApprovalStatus.APPROVED, "Go")


def test_decline_keeps_the_stage(application: Any, company_ta: Any, company_admin: Any) -> None:
    approval = request_approval(
        application, requested_by=company_ta, approver=company_admin, note=""
    )
    decide_approval(approval, approve=False, note="Not senior enough")
    application.refresh_from_db()
    approval.refresh_from_db()
    assert application.stage == Stage.NEW
    assert (approval.status, approval.decision_note) == (
        ApprovalStatus.DECLINED,
        "Not senior enough",
    )
    assert approval.decided_at is not None


def test_deciding_twice_is_a_conflict(
    application: Any, company_ta: Any, company_admin: Any
) -> None:
    approval = request_approval(
        application, requested_by=company_ta, approver=company_admin, note=""
    )
    decide_approval(approval, approve=False, note="No")
    with pytest.raises(StageConflict):
        decide_approval(approval, approve=True, note="")


def test_post_message_broadcasts_after_commit(
    application: Any,
    company_ta: Any,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    sent: list[Any] = []
    monkeypatch.setattr(discussion, "broadcast_message", sent.append)
    with django_capture_on_commit_callbacks(execute=True):
        message = discussion.post_message(
            application, author=company_ta, body="Can he start in May?"
        )
    assert sent == [message]
    assert message.application == application
    assert message.kind == MessageKind.USER


def test_interview_times_render_in_vietnam_time(application: Any, company_ta: Any) -> None:
    start = timezone.make_aware(datetime(2026, 9, 22, 14, 30), timezone.get_default_timezone())
    interview = book(
        at_stage(application, Stage.SCREEN),
        company_ta,
        starts_at=start,
        ends_at=start + timedelta(minutes=45),
        format=InterviewFormat.IN_PERSON,
        location="",
    )
    message = application.messages.get(body__startswith="Interview round")
    assert message.body == "Interview round 1 · 22/09/2026 14:30–15:15 · In person"
    assert interview.format == InterviewFormat.IN_PERSON
