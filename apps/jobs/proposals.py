"""Change proposals (spec §5): a member who may not change a posted job asks an admin to.

Like `lifecycle`, every function locks its row, checks visibility (404) and then the permission
(403) on the locked row. Validating a proposal never saves and never runs a lifecycle
transition: `intent`, `team`, `status` and the bounty never reach the serializer.
"""

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.companies.access import membership_of
from apps.companies.models import CompanyRole
from apps.jobs.access import NOT_YOURS, has_live_applications, jobs_visible_to
from apps.jobs.lifecycle import (
    DELETE_BLOCKED_MESSAGE,
    JobRuleError,
    close_job,
    delete_job,
    locked_job,
    reopen_job,
)
from apps.jobs.models import ChangeKind, ChangeStatus, Job, JobChangeRequest, JobStatus
from apps.jobs.notifications import notify_admins_change_requested, notify_requester_change_decided
from apps.jobs.serializers import JobSerializer

ALREADY_DECIDED = "This proposal was already decided."
NOTHING_TO_CHANGE = "Change at least one field of the job."
NOTE_REQUIRED = "Tell them why."
JOB_CHANGED = "This job has changed since the proposal. Reject it instead."

# Not proposable: the bounty is Sparktal's, the rest is set by the system or by an admin.
_NEVER_PROPOSED = frozenset(
    {"intent", "team", "status", "bounty_type", "bounty_pct", "bounty_amount"}
)


def editable_fields() -> frozenset[str]:
    meta = JobSerializer.Meta
    return frozenset(meta.fields) - frozenset(meta.read_only_fields) - _NEVER_PROPOSED


def is_admin(user: User | None) -> bool:
    membership = membership_of(user)
    return membership is not None and membership.role == CompanyRole.ADMIN


def change_requests_visible_to(user: User | None) -> QuerySet[JobChangeRequest]:
    """Admins see every request on a job they see; a requester sees their own."""
    requests = JobChangeRequest.objects.filter(job__in=jobs_visible_to(user))
    if is_admin(user):
        return requests
    if user is None:
        return requests.none()
    return requests.filter(requested_by=user)


def _validated(job: Job, changes: Mapping[str, Any], user: User) -> JobSerializer:
    """`changes` checked against `job` as `user`. Nothing is saved, nothing transitions."""
    serializer = JobSerializer(
        job,
        data={**changes, "intent": "save"},
        partial=True,
        context={"request": SimpleNamespace(user=user)},
    )
    serializer.is_valid(raise_exception=True)
    return serializer


def _kept(changes: Mapping[str, Any] | None) -> dict[str, Any]:
    allowed = editable_fields()
    return {key: value for key, value in (changes or {}).items() if key in allowed}


@transaction.atomic
def propose_change(
    job: Job, *, actor: User, kind: str, changes: dict[str, Any] | None = None
) -> JobChangeRequest:
    locked, _ = locked_job(job, actor, lambda p: p.propose)
    stored: dict[str, Any] = {}
    if kind == ChangeKind.EDIT:
        stored = _kept(changes)
        if not stored:
            raise ValidationError({"changes": [NOTHING_TO_CHANGE]})
        _validated(locked, stored, actor)
    elif kind == ChangeKind.CLOSE:
        if locked.status != JobStatus.ACTIVE:
            raise PermissionDenied(NOT_YOURS)
    elif kind == ChangeKind.REOPEN:
        if locked.status != JobStatus.CLOSED:
            raise PermissionDenied(NOT_YOURS)
    elif kind == ChangeKind.DELETE:
        if locked.status != JobStatus.CLOSED or has_live_applications(locked):
            raise JobRuleError(DELETE_BLOCKED_MESSAGE)
    else:
        raise ValidationError({"kind": ["Choose edit, close, reopen or delete."]})
    JobChangeRequest.objects.filter(
        job=locked, requested_by=actor, status=ChangeStatus.PENDING
    ).update(status=ChangeStatus.WITHDRAWN)
    request = JobChangeRequest.objects.create(
        job=locked, requested_by=actor, kind=kind, changes=stored
    )
    transaction.on_commit(lambda: notify_admins_change_requested(request), robust=True)
    return request


def _locked_request(request: JobChangeRequest, actor: User | None) -> JobChangeRequest:
    """The request row, locked, once the actor may see it (else 404).

    The job is locked first, as `propose_change` does, so the two cannot deadlock.
    """
    if actor is None or not change_requests_visible_to(actor).filter(pk=request.pk).exists():
        raise Http404
    list(Job.objects.select_for_update().filter(pk=request.job_id).values_list("pk", flat=True))
    try:
        return (
            JobChangeRequest.objects.select_for_update(of=("self",))
            .select_related("job")
            .get(pk=request.pk)
        )
    except JobChangeRequest.DoesNotExist:
        raise Http404 from None


def _require_pending(request: JobChangeRequest) -> None:
    if request.status != ChangeStatus.PENDING:
        raise JobRuleError(ALREADY_DECIDED)


def _decide(request: JobChangeRequest, actor: User, status: str, note: str = "") -> None:
    request.status = status
    request.reviewed_by = actor
    request.reviewed_at = timezone.now()
    request.review_note = note
    request.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])
    transaction.on_commit(lambda: notify_requester_change_decided(request), robust=True)


@transaction.atomic
def approve_change(request: JobChangeRequest, *, actor: User) -> Job:
    locked = _locked_request(request, actor)
    job, _ = locked_job(locked.job, actor, lambda p: p.review_proposals)
    _require_pending(locked)
    if locked.kind == ChangeKind.CLOSE and job.status != JobStatus.ACTIVE:
        raise JobRuleError(JOB_CHANGED)
    if locked.kind == ChangeKind.REOPEN and job.status != JobStatus.CLOSED:
        raise JobRuleError(JOB_CHANGED)
    if locked.kind == ChangeKind.EDIT:
        # Against the job as it is now: it may have moved on since the proposal was filed.
        _validated(job, locked.changes, actor).save()
    elif locked.kind == ChangeKind.CLOSE:
        close_job(job, actor=actor)
    elif locked.kind == ChangeKind.REOPEN:
        reopen_job(job, actor=actor)
    else:
        delete_job(job, actor=actor)
    job.refresh_from_db()
    locked.job = job  # the decision email names the job as saved
    _decide(locked, actor, ChangeStatus.APPROVED)
    request.refresh_from_db()
    return job


@transaction.atomic
def reject_change(request: JobChangeRequest, *, actor: User, note: str) -> JobChangeRequest:
    locked = _locked_request(request, actor)
    locked_job(locked.job, actor, lambda p: p.review_proposals)
    _require_pending(locked)
    if not note.strip():
        raise ValidationError({"note": [NOTE_REQUIRED]})
    _decide(locked, actor, ChangeStatus.REJECTED, note.strip())
    return locked


@transaction.atomic
def withdraw_change(request: JobChangeRequest, *, actor: User) -> JobChangeRequest:
    locked = _locked_request(request, actor)
    if locked.requested_by_id != actor.pk:
        raise PermissionDenied(NOT_YOURS)
    _require_pending(locked)
    locked.status = ChangeStatus.WITHDRAWN
    locked.save(update_fields=["status"])
    return locked
