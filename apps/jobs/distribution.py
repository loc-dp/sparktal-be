"""Sparktal's side of a job (spec §8, §9): the bounty, the channels and the recruiter invites.

A posted job waits in `pending` until Sparktal sets its bounty and distributes it to the
marketplace, to invited recruiters, or both. Every function here locks the job row first and
only then any invite or assignment row, the order `lifecycle.close_job` uses too, so two calls
on one job wait for each other instead of deadlocking or missing each other's rows. Who may call
these (Sparktal staff, or the invited recruiter) is the caller's check, except that a recruiter
can only ever act on their own invite.
"""

from collections.abc import Sequence
from decimal import Decimal
from functools import partial
from uuid import UUID

from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from apps.accounts.models import User
from apps.chat.models import Channel
from apps.chat.signals import ensure_channel
from apps.jobs.bounty import clean_bounty, has_bounty
from apps.jobs.marketplace import marketplace_queryset
from apps.jobs.models import (
    PRIVATE_STATUSES,
    InviteStatus,
    Job,
    JobAssignment,
    JobInvite,
    JobStatus,
)
from apps.jobs.notifications import notify_recruiter_invited
from apps.recruiters.models import RecruiterProfile, RecruiterStatus

NOT_POSTED = "This job hasn't been posted by the company yet."
NOT_WAITING = "This job is no longer waiting for distribution."
NEEDS_BOUNTY = "Set the bounty before distributing."
NEEDS_CHANNEL = "Pick the marketplace, at least one recruiter, or both."
NOT_DISTRIBUTED = "This job hasn't been distributed yet."
NOT_LIVE = "Only a live job can take invites."
ONLY_APPROVED = "Only approved recruiters can be invited."
INVITE_CLOSED = "This invite is no longer open."
DECLINE_REASON_MAX = 500


class DistributionError(APIException):
    status_code = 400
    default_code = "distribution"
    default_detail = "This job can't be distributed that way."


def _locked(job: Job) -> Job:
    return Job.objects.select_for_update().get(pk=job.pk)


@transaction.atomic
def set_bounty(
    job: Job,
    *,
    bounty_type: str | None,
    bounty_pct: int | None = None,
    bounty_amount: Decimal | None = None,
) -> Job:
    """Set the bounty; it applies at once to every recruiter on the job (no per-CV snapshot)."""
    locked = _locked(job)
    if locked.status in PRIVATE_STATUSES or locked.deleted_at is not None:
        raise DistributionError(NOT_POSTED)
    values = clean_bounty(bounty_type, bounty_pct, bounty_amount)
    for name, value in values.items():
        setattr(locked, name, value)
    locked.save(update_fields=[*values, "updated_at"])
    return locked


@transaction.atomic
def distribute_job(
    job: Job, *, actor: User, on_marketplace: bool, recruiters: Sequence[RecruiterProfile]
) -> Job:
    """Take a job out of the Sparktal queue and live, on the channels picked."""
    locked = _locked(job)
    if locked.status != JobStatus.PENDING or locked.deleted_at is not None:
        raise DistributionError(NOT_WAITING)
    if not has_bounty(locked):
        raise DistributionError(NEEDS_BOUNTY)
    if not on_marketplace and not recruiters:
        raise DistributionError(NEEDS_CHANNEL)
    locked.status = JobStatus.ACTIVE
    locked.published_at = timezone.now()
    locked.on_marketplace = on_marketplace
    locked.save(update_fields=["status", "published_at", "on_marketplace", "updated_at"])
    invite_recruiters(locked, actor=actor, recruiters=recruiters)
    return locked


@transaction.atomic
def set_marketplace(job: Job, *, on: bool) -> Job:
    """Turning the marketplace off only stops new joins: recruiters already on it stay."""
    locked = _locked(job)
    if locked.status not in (JobStatus.ACTIVE, JobStatus.CLOSED) or locked.deleted_at is not None:
        raise DistributionError(NOT_DISTRIBUTED)
    locked.on_marketplace = on
    locked.save(update_fields=["on_marketplace", "updated_at"])
    return locked


@transaction.atomic
def invite_recruiters(
    job: Job, *, actor: User, recruiters: Sequence[RecruiterProfile]
) -> list[JobInvite]:
    """Invite each recruiter not already on the job; the ones newly invited are emailed.

    An invite they declined, Sparktal cancelled, or accepted before leaving the job is opened
    again. A pending one is left as it is.
    """
    locked = _locked(job)
    if locked.status != JobStatus.ACTIVE or locked.deleted_at is not None:
        raise DistributionError(NOT_LIVE)
    if not has_bounty(locked):
        # The invite shows the bounty, and recruiters never see a job without one.
        raise DistributionError(NEEDS_BOUNTY)
    wanted = list(dict.fromkeys(recruiter.pk for recruiter in recruiters))
    approved = RecruiterProfile.objects.filter(pk__in=wanted, status=RecruiterStatus.APPROVED)
    if approved.count() != len(wanted):
        raise ValidationError({"recruiters": [ONLY_APPROVED]})
    working = set(
        JobAssignment.objects.filter(
            job=locked, recruiter_id__in=wanted, left_at__isnull=True
        ).values_list("recruiter_id", flat=True)
    )
    existing = {
        invite.recruiter_id: invite
        for invite in JobInvite.objects.select_for_update().filter(
            job=locked, recruiter_id__in=wanted
        )
    }
    invited: list[JobInvite] = []
    for recruiter_id in wanted:
        if recruiter_id in working:
            continue
        invite = existing.get(recruiter_id)
        if invite is None:
            invite = JobInvite.objects.create(
                job=locked, recruiter_id=recruiter_id, invited_by=actor
            )
        elif invite.status == InviteStatus.PENDING:
            continue
        else:
            invite.status = InviteStatus.PENDING
            invite.responded_at = None
            invite.decline_reason = ""
            invite.invited_by = actor
            # "Newest first" in the recruiter's list follows when the invite was last sent.
            invite.created_at = timezone.now()
            invite.save(
                update_fields=[
                    "status",
                    "responded_at",
                    "decline_reason",
                    "invited_by",
                    "created_at",
                ]
            )
        invited.append(invite)
    for invite in invited:
        transaction.on_commit(partial(notify_recruiter_invited, invite), robust=True)
    return invited


@transaction.atomic
def cancel_invites(job: Job, *, invite_ids: Sequence[UUID]) -> int:
    """Withdraw invites nobody answered yet; the number withdrawn."""
    locked = _locked(job)
    return JobInvite.objects.filter(
        job=locked, pk__in=invite_ids, status=InviteStatus.PENDING
    ).update(status=InviteStatus.CANCELLED)


@transaction.atomic
def join_job(job: Job, recruiter: RecruiterProfile) -> tuple[JobAssignment, bool]:
    """Put the recruiter on the job (again, if they left), with their chat channel.

    However they came in, an invite still waiting for their answer is now accepted. Under the
    job lock, so `invite_recruiters` either sees the assignment or has made its invite already.
    """
    locked = _locked(job)
    assignment, created = JobAssignment.objects.get_or_create(job=locked, recruiter=recruiter)
    if not created and assignment.left_at is not None:
        assignment.left_at = None
        assignment.save(update_fields=["left_at"])
    ensure_channel(locked, recruiter)
    JobInvite.objects.filter(job=locked, recruiter=recruiter, status=InviteStatus.PENDING).update(
        status=InviteStatus.ACCEPTED, responded_at=timezone.now()
    )
    return assignment, created


@transaction.atomic
def join_marketplace_job(
    job_id: UUID | str, recruiter: RecruiterProfile
) -> tuple[JobAssignment, bool, Channel]:
    """Join from the marketplace: only a job that is on it, checked while holding its lock.

    The lookup is the lock (`FOR UPDATE OF` the job row), and Postgres re-reads the row's
    conditions once it gets the lock, so a close or marketplace-off that commits while this
    waits is seen and the join is a 404, not an assignment on a job that just left. An invited
    recruiter on a direct-only job gets the same 404: they accept the invite instead.
    """
    locked = (
        marketplace_queryset(recruiter).select_for_update(of=("self",)).filter(pk=job_id).first()
    )
    if locked is None:
        raise Http404
    assignment, created = join_job(locked, recruiter)
    # A plain read, not a second `ensure_channel`: `join_job` has just made sure it exists.
    return assignment, created, Channel.objects.get(job=locked, recruiter=recruiter)


def _locked_with_job(invite: JobInvite, recruiter: RecruiterProfile) -> tuple[Job, JobInvite]:
    """The invite's job, then the invite, both locked, once the invite is `recruiter`'s.

    Another recruiter's invite does not exist as far as `recruiter` can tell (404).
    """
    if invite.recruiter_id != recruiter.pk:
        raise Http404
    job = Job.objects.select_for_update().filter(pk=invite.job_id).first()
    locked = JobInvite.objects.select_for_update().filter(pk=invite.pk).first()
    if job is None or locked is None:
        raise Http404
    return job, locked


@transaction.atomic
def accept_invite(invite: JobInvite, *, recruiter: RecruiterProfile) -> JobAssignment:
    job, locked = _locked_with_job(invite, recruiter)
    if (
        locked.status != InviteStatus.PENDING
        or job.status != JobStatus.ACTIVE
        or job.deleted_at is not None
        or not has_bounty(job)
    ):
        raise DistributionError(INVITE_CLOSED)
    assignment, _ = join_job(job, recruiter)
    invite.refresh_from_db()
    return assignment


@transaction.atomic
def decline_invite(
    invite: JobInvite, *, recruiter: RecruiterProfile, reason: str = ""
) -> JobInvite:
    _, locked = _locked_with_job(invite, recruiter)
    if locked.status != InviteStatus.PENDING:
        raise DistributionError(INVITE_CLOSED)
    locked.status = InviteStatus.DECLINED
    locked.decline_reason = reason.strip()[:DECLINE_REASON_MAX]
    locked.responded_at = timezone.now()
    locked.save(update_fields=["status", "decline_reason", "responded_at"])
    return locked
