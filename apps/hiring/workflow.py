"""The only place stage rules live: which commands an application allows, and running them.

Views ask `available_actions` for the buttons and call the commands; a command that no
longer fits the stage (someone else moved the candidate first) raises StageConflict.
"""

from datetime import datetime
from uuid import UUID

from django.db import IntegrityError, models, transaction
from django.db.models import Max
from django.utils import timezone

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.hiring.discussion import post_system_message
from apps.hiring.models import ApprovalRequest, ApprovalStatus, Interview, InterviewFormat
from apps.jobs.access import can_view_job

STAGE_MOVED = (
    "This candidate has moved on since you opened the page. Refresh to see where they are now."
)
ALREADY_PENDING = "An approval request is already waiting for this candidate."
ALREADY_DECIDED = "This approval request has already been decided."


class Action(models.TextChoices):
    MOVE_FORWARD = "move_forward", "Move forward"
    SET_INTERVIEW = "set_interview", "Set interview"
    NEXT_INTERVIEW = "next_interview", "Next interview"
    REQUEST_APPROVAL = "request_approval", "Request approval"
    OFFER = "offer", "Offer"
    HIRE = "hire", "Mark hired"
    ARCHIVE = "archive", "Archive"


APPLICANT_STAGES = frozenset({Stage.SHORTLISTED, Stage.NEW})
# Where an approved request takes the application.
APPROVED_NEXT = {
    Stage.SHORTLISTED: Stage.SCREEN,
    Stage.NEW: Stage.SCREEN,
    Stage.SCREEN: Stage.INTERVIEW,
}


class StageConflict(Exception):
    """The command does not fit the application's current stage."""


def _waiting(application: Application) -> ApprovalRequest | None:
    return (
        application.approval_requests.filter(status=ApprovalStatus.PENDING)
        .select_related("approver", "requested_by")
        .first()
    )


def _approver_gone(approval: ApprovalRequest) -> bool:
    """An approver who was deactivated or lost the job can never decide."""
    approver = approval.approver
    return not approver.is_active or not can_view_job(approver, approval.application.job)


def pending_approval(application: Application) -> ApprovalRequest | None:
    """The open request, unless its approver can no longer decide it."""
    approval = _waiting(application)
    if approval is None or _approver_gone(approval):
        return None
    return approval


def last_decline(application: Application) -> ApprovalRequest | None:
    """The newest decline at the current stage, until someone asks again or the stage moves."""
    if pending_approval(application) is not None:
        return None
    return (
        application.approval_requests.filter(
            status=ApprovalStatus.DECLINED, stage=application.stage
        )
        .select_related("approver")
        .order_by("-decided_at")
        .first()
    )


def available_actions(application: Application) -> list[Action]:
    """Primary action first, then the secondary one, then Archive."""
    stage = application.stage
    if stage in APPLICANT_STAGES:
        actions = [Action.MOVE_FORWARD, Action.REQUEST_APPROVAL]
    elif stage == Stage.SCREEN:
        actions = [Action.SET_INTERVIEW, Action.REQUEST_APPROVAL]
    elif stage == Stage.INTERVIEW:
        has_rounds = application.interviews.exists()
        actions = [Action.NEXT_INTERVIEW if has_rounds else Action.SET_INTERVIEW, Action.OFFER]
    elif stage == Stage.OFFER:
        actions = [Action.HIRE]
    else:
        return []
    if Action.REQUEST_APPROVAL in actions and pending_approval(application) is not None:
        actions.remove(Action.REQUEST_APPROVAL)
    return [*actions, Action.ARCHIVE]


def _locked(application_id: UUID) -> Application:
    return Application.objects.select_for_update().get(pk=application_id)


def _require(application: Application, *allowed: Action) -> None:
    if not set(allowed) & set(available_actions(application)):
        raise StageConflict(STAGE_MOVED)


def cancel_pending_approvals(application: Application) -> None:
    ApprovalRequest.objects.filter(application=application, status=ApprovalStatus.PENDING).update(
        status=ApprovalStatus.CANCELLED, decided_at=timezone.now()
    )


def _move(application: Application, stage: str) -> None:
    application.move_to(stage)
    cancel_pending_approvals(application)


def move_forward(application: Application) -> None:
    with transaction.atomic():
        current = _locked(application.pk)
        _require(current, Action.MOVE_FORWARD)
        _move(current, Stage.SCREEN)


def make_offer(application: Application) -> None:
    with transaction.atomic():
        current = _locked(application.pk)
        _require(current, Action.OFFER)
        _move(current, Stage.OFFER)


def mark_hired(application: Application) -> None:
    with transaction.atomic():
        current = _locked(application.pk)
        _require(current, Action.HIRE)
        _move(current, Stage.ONBOARDED)


def archive(application: Application, reason: str) -> None:
    with transaction.atomic():
        current = _locked(application.pk)
        _require(current, Action.ARCHIVE)
        current.reject(reason)
        cancel_pending_approvals(current)


def interview_summary(interview: Interview) -> str:
    starts = timezone.localtime(interview.starts_at)
    ends = timezone.localtime(interview.ends_at)
    parts = [
        f"Interview round {interview.round}",
        f"{starts:%d/%m/%Y %H:%M}–{ends:%H:%M}",
        InterviewFormat(interview.format).label,
    ]
    if interview.location:
        parts.append(interview.location)
    return " · ".join(parts)


def schedule_interview(
    application: Application,
    *,
    created_by: User,
    starts_at: datetime,
    ends_at: datetime,
    format: str,
    location: str,
) -> Interview:
    with transaction.atomic():
        current = _locked(application.pk)
        _require(current, Action.SET_INTERVIEW, Action.NEXT_INTERVIEW)
        last = current.interviews.aggregate(last=Max("round"))["last"] or 0
        interview = Interview.objects.create(
            application=current,
            round=last + 1,
            starts_at=starts_at,
            ends_at=ends_at,
            format=format,
            location=location,
            created_by=created_by,
        )
        if current.stage != Stage.INTERVIEW:
            _move(current, Stage.INTERVIEW)
        post_system_message(current, interview_summary(interview))
    return interview


def request_approval(
    application: Application, *, requested_by: User, approver: User, note: str
) -> ApprovalRequest:
    with transaction.atomic():
        current = _locked(application.pk)
        waiting = _waiting(current)
        if waiting is not None:
            if not _approver_gone(waiting):
                raise StageConflict(ALREADY_PENDING)
            # Nobody can decide it any more, so it must not block the new request.
            waiting.status = ApprovalStatus.CANCELLED
            waiting.decided_at = timezone.now()
            waiting.save(update_fields=["status", "decided_at"])
        _require(current, Action.REQUEST_APPROVAL)
        try:
            with transaction.atomic():
                return ApprovalRequest.objects.create(
                    application=current,
                    requested_by=requested_by,
                    approver=approver,
                    stage=current.stage,
                    note=note,
                )
        except IntegrityError as error:
            raise StageConflict(ALREADY_PENDING) from error


def decide_approval(approval: ApprovalRequest, *, approve: bool, note: str) -> None:
    with transaction.atomic():
        # Same lock order as every other command: the application first, then the request.
        application = _locked(approval.application_id)
        current = ApprovalRequest.objects.select_for_update().get(pk=approval.pk)
        if current.status != ApprovalStatus.PENDING:
            raise StageConflict(ALREADY_DECIDED)
        if application.stage != current.stage or current.stage not in APPROVED_NEXT:
            raise StageConflict(STAGE_MOVED)
        current.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DECLINED
        current.decision_note = note
        current.decided_at = timezone.now()
        current.save(update_fields=["status", "decision_note", "decided_at"])
        if approve:
            _move(application, APPROVED_NEXT[Stage(current.stage)])
