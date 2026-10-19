"""The company-side job lifecycle (spec §5): draft, submit, post, close, reopen, delete.

The API and Django Admin never set `status` directly; they call these functions. Each one locks
the job row, then checks `job_permissions` on the locked row: a job the actor cannot see is a
404, one they can see but may not act on is a 403.
"""

from collections.abc import Callable, Mapping
from typing import Any

from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.jobs.access import (
    NOT_YOURS,
    JobPermissions,
    can_view_job,
    job_permissions,
)
from apps.jobs.bounty import has_bounty
from apps.jobs.models import (
    PRIVATE_STATUSES,
    ChangeStatus,
    InviteStatus,
    Job,
    JobStatus,
    WorkModel,
)
from apps.jobs.notifications import notify_admins_job_submitted, notify_ops_job_queued

REQUIRED_MESSAGES = {
    "title": "Enter a job title",
    "why_we_need_you": "Tell recruiters why this role exists",
    "roles": "List the main responsibilities",
    "qualifications": "Add the must-have qualifications",
}
# Brief answers a new job must give; jobs posted before the brief may still lack them.
BRIEF_REQUIRED_MESSAGES = {
    "role_type": "Say whether this is a new position or a replacement",
    "working_hours": "Select the working hours",
    "languages": "Select at least one language",
}
OFFICE_REQUIRED_MESSAGE = "Select an office for this job"
DELETE_BLOCKED_MESSAGE = "Close the job and finish or archive every candidate before deleting it."


class JobRuleError(APIException):
    status_code = 400
    default_code = "job_rule"
    default_detail = "This job cannot be changed that way."


def _blank(value: Any) -> bool:
    return not (value.strip() if isinstance(value, str) else value)


def missing_fields(values: Mapping[str, Any], *, brief: bool = True) -> dict[str, list[str]]:
    """What a job must still answer before it can be sent or posted, by field."""
    required = {**REQUIRED_MESSAGES, **(BRIEF_REQUIRED_MESSAGES if brief else {})}
    missing = {field: [message] for field, message in required.items() if _blank(values.get(field))}
    if values.get("work_model") != WorkModel.REMOTE and values.get("office") is None:
        missing["office"] = [OFFICE_REQUIRED_MESSAGE]
    return missing


def missing_for_posting(job: Job) -> dict[str, list[str]]:
    values = {
        field: getattr(job, field)
        for field in (*REQUIRED_MESSAGES, *BRIEF_REQUIRED_MESSAGES, "work_model")
    }
    values["office"] = job.office_id
    return missing_fields(values)


def locked_job(
    job: Job, actor: User | None, allowed: Callable[[JobPermissions], bool]
) -> tuple[Job, JobPermissions]:
    """The job row, locked, once the actor may do what `allowed` names to it."""
    try:
        locked = Job.objects.select_for_update().get(pk=job.pk)
    except Job.DoesNotExist:
        raise Http404 from None
    if not can_view_job(actor, locked):
        raise Http404
    permissions = job_permissions(actor, locked)
    if not allowed(permissions):
        raise PermissionDenied(NOT_YOURS)
    return locked, permissions


def _locked(job: Job, actor: User | None, allowed: Callable[[JobPermissions], bool]) -> Job:
    return locked_job(job, actor, allowed)[0]


def _require_complete(job: Job) -> None:
    missing = missing_for_posting(job)
    if missing:
        raise ValidationError(missing)


@transaction.atomic
def submit_job(job: Job, *, actor: User) -> Job:
    job = _locked(job, actor, lambda p: p.submit)
    _require_complete(job)
    job.status = JobStatus.SUBMITTED
    job.save(update_fields=["status", "updated_at"])
    transaction.on_commit(lambda: notify_admins_job_submitted(job), robust=True)
    return job


@transaction.atomic
def withdraw_job(job: Job, *, actor: User) -> Job:
    job = _locked(job, actor, lambda p: p.withdraw)
    job.status = JobStatus.DRAFT
    job.save(update_fields=["status", "updated_at"])
    return job


@transaction.atomic
def post_job(job: Job, *, actor: User) -> Job:
    job = _locked(job, actor, lambda p: p.post)
    if job.status not in PRIVATE_STATUSES:
        raise PermissionDenied(NOT_YOURS)
    _require_complete(job)
    job.status = JobStatus.PENDING
    job.posted_at = timezone.now()
    job.save(update_fields=["status", "posted_at", "updated_at"])
    transaction.on_commit(lambda: notify_ops_job_queued(job), robust=True)
    return job


@transaction.atomic
def close_job(job: Job, *, actor: User) -> Job:
    job = _locked(job, actor, lambda p: p.close)
    job.status = JobStatus.CLOSED
    job.closed_at = timezone.now()
    job.save(update_fields=["status", "closed_at", "updated_at"])
    # Recruiters already on it keep their place; an offer nobody took up is withdrawn.
    job.invites.filter(status=InviteStatus.PENDING).update(status=InviteStatus.CANCELLED)
    return job


@transaction.atomic
def reopen_job(job: Job, *, actor: User) -> Job:
    """Back on the channels it had, with its recruiters (spec §5).

    A job without a bounty (legacy data) goes back to the Sparktal queue instead.
    """
    job = _locked(job, actor, lambda p: p.reopen)
    job.closed_at = None
    if has_bounty(job):
        job.status = JobStatus.ACTIVE
        job.published_at = job.published_at or timezone.now()
        job.save(update_fields=["status", "closed_at", "published_at", "updated_at"])
        return job
    job.status = JobStatus.PENDING
    job.posted_at = job.posted_at or timezone.now()
    job.save(update_fields=["status", "closed_at", "posted_at", "updated_at"])
    transaction.on_commit(lambda: notify_ops_job_queued(job), robust=True)
    return job


@transaction.atomic
def delete_job(job: Job, *, actor: User) -> None:
    locked, permissions = locked_job(job, actor, lambda p: p.delete or p.close or p.reopen)
    if not permissions.delete:
        # An active or closed job the actor runs: it is not ready to go, say what is missing.
        raise JobRuleError(DELETE_BLOCKED_MESSAGE)
    locked.deleted_at = timezone.now()
    locked.save(update_fields=["deleted_at", "updated_at"])
    # Nobody can decide a proposal on a job that is gone.
    locked.change_requests.filter(status=ChangeStatus.PENDING).update(status=ChangeStatus.WITHDRAWN)


@transaction.atomic
def restore_job(job: Job) -> None:
    """Django Admin only: bring a deleted job back."""
    locked = Job.objects.select_for_update().get(pk=job.pk)
    locked.deleted_at = None
    locked.save(update_fields=["deleted_at", "updated_at"])
