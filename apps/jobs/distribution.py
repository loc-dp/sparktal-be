"""Sparktal's side of a job (spec §8, §9): the bounty, and the headhunters it assigns.

A posted job waits in `pending` until Sparktal sets its bounty and distributes it to at least
one headhunter; from then on Sparktal assigns more or removes them, and a live job always keeps
one. The marketplace channel is used only with `settings.MARKETPLACE_ENABLED`. Every function
here locks the job row first and only then any assignment row, the order `lifecycle.close_job`
uses too, so two calls on one job wait for each other instead of deadlocking or missing each
other's rows. Who may call these (Sparktal staff, or a recruiter joining from the
marketplace) is the caller's check.
"""

import logging
from collections.abc import Sequence
from decimal import Decimal
from functools import partial
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from apps.accounts.models import User
from apps.chat.models import Channel
from apps.chat.signals import ensure_channel
from apps.jobs.bounty import clean_bounty, has_bounty
from apps.jobs.marketplace import marketplace_queryset
from apps.jobs.models import PRIVATE_STATUSES, Job, JobAssignment, JobStatus
from apps.jobs.notifications import notify_recruiter_assigned
from apps.recruiters.models import RecruiterProfile, RecruiterStatus

logger = logging.getLogger(__name__)

NOT_POSTED = "This job hasn't been posted by the company yet."
NOT_WAITING = "This job is no longer waiting for distribution."
NEEDS_BOUNTY = "Set the bounty before distributing."
NEEDS_RECRUITER = "Pick at least one recruiter."
NOT_DISTRIBUTED = "This job hasn't been distributed yet."
NOT_LIVE = "Only a live job can take recruiters."
ONLY_APPROVED = "Only approved recruiters can be assigned."
MARKETPLACE_OFF = "The marketplace is switched off."
LAST_RECRUITER = "A live job needs at least one recruiter. Assign someone else first."


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
    guarantee_days: int | None = None,
) -> Job:
    """Set the bounty; it applies at once to every recruiter on the job (no per-CV snapshot).

    `guarantee_days`, when given, is set with it. It only reaches placements made afterwards:
    each one copies the days when the candidate is hired.
    """
    locked = _locked(job)
    if locked.status in PRIVATE_STATUSES or locked.deleted_at is not None:
        raise DistributionError(NOT_POSTED)
    values = clean_bounty(bounty_type, bounty_pct, bounty_amount)
    if guarantee_days is not None:
        values["guarantee_days"] = guarantee_days
    for name, value in values.items():
        setattr(locked, name, value)
    locked.save(update_fields=[*values, "updated_at"])
    return locked


@transaction.atomic
def distribute_job(
    job: Job,
    *,
    actor: User,
    recruiters: Sequence[RecruiterProfile],
    on_marketplace: bool = False,
) -> Job:
    """Take a job out of the Sparktal queue and live, with the headhunters picked on it.

    `on_marketplace` is kept only while the marketplace is enabled; otherwise the job is off it.
    """
    locked = _locked(job)
    if locked.status != JobStatus.PENDING or locked.deleted_at is not None:
        raise DistributionError(NOT_WAITING)
    if not has_bounty(locked):
        raise DistributionError(NEEDS_BOUNTY)
    if not recruiters:
        raise DistributionError(NEEDS_RECRUITER)
    locked.status = JobStatus.ACTIVE
    locked.published_at = timezone.now()
    locked.on_marketplace = on_marketplace and settings.MARKETPLACE_ENABLED
    locked.save(update_fields=["status", "published_at", "on_marketplace", "updated_at"])
    assign_recruiters(locked, actor=actor, recruiters=recruiters)
    return locked


@transaction.atomic
def set_marketplace(job: Job, *, on: bool) -> Job:
    """Turning the marketplace off only stops new joins: recruiters already on it stay.

    Putting a job on it is refused while the marketplace is switched off.
    """
    locked = _locked(job)
    if locked.status not in (JobStatus.ACTIVE, JobStatus.CLOSED) or locked.deleted_at is not None:
        raise DistributionError(NOT_DISTRIBUTED)
    if on and not settings.MARKETPLACE_ENABLED:
        raise DistributionError(MARKETPLACE_OFF)
    locked.on_marketplace = on
    locked.save(update_fields=["on_marketplace", "updated_at"])
    return locked


@transaction.atomic
def assign_recruiters(
    job: Job, *, actor: User, recruiters: Sequence[RecruiterProfile]
) -> list[JobAssignment]:
    """Put each recruiter on the live job at once; the ones newly put on are emailed.

    One Sparktal removed earlier is put back on the same assignment. One already on the job is
    left as they are. The ones newly put on are returned, in the order given.
    """
    locked = _locked(job)
    if locked.status != JobStatus.ACTIVE or locked.deleted_at is not None:
        raise DistributionError(NOT_LIVE)
    if not has_bounty(locked):
        # The email shows the bounty, and recruiters never see a job without one.
        raise DistributionError(NEEDS_BOUNTY)
    wanted = list(dict.fromkeys(recruiter.pk for recruiter in recruiters))
    approved = RecruiterProfile.objects.filter(
        pk__in=wanted, status=RecruiterStatus.APPROVED
    ).in_bulk()
    if len(approved) != len(wanted):
        raise ValidationError({"recruiters": [ONLY_APPROVED]})
    existing = {
        assignment.recruiter_id: assignment
        for assignment in JobAssignment.objects.select_for_update().filter(
            job=locked, recruiter_id__in=wanted
        )
    }
    assigned: list[JobAssignment] = []
    for recruiter_id in wanted:
        assignment = existing.get(recruiter_id)
        if assignment is None:
            assignment = JobAssignment.objects.create(job=locked, recruiter=approved[recruiter_id])
        elif assignment.left_at is not None:
            assignment.left_at = None
            assignment.joined_at = timezone.now()  # back on the job: the held-first order restarts
            assignment.save(update_fields=["left_at", "joined_at"])
        else:
            continue
        ensure_channel(locked, approved[recruiter_id])
        assigned.append(assignment)
    if assigned:
        logger.info(
            "%s assigned recruiters %s to job %s",
            actor.pk,
            [str(assignment.recruiter_id) for assignment in assigned],
            locked.pk,
        )
    for assignment in assigned:
        transaction.on_commit(partial(notify_recruiter_assigned, assignment), robust=True)
    return assigned


@transaction.atomic
def unassign_recruiter(job: Job, *, recruiter_id: UUID) -> JobAssignment:
    """Take the recruiter off the job. Their CVs on it stay, and they are not emailed.

    A live job keeps at least one recruiter. Both rows are locked, the job first, so two
    removals at once cannot each see the other still on the job and leave it with nobody.
    """
    locked = _locked(job)
    assignment = (
        JobAssignment.objects.select_for_update()
        .filter(job=locked, recruiter_id=recruiter_id, left_at__isnull=True)
        .first()
    )
    if assignment is None:
        raise Http404
    if locked.status == JobStatus.ACTIVE:
        held = JobAssignment.objects.filter(job=locked, left_at__isnull=True).count()
        if held <= 1:
            raise DistributionError(LAST_RECRUITER)
    assignment.left_at = timezone.now()
    assignment.save(update_fields=["left_at"])
    return assignment


@transaction.atomic
def join_job(job: Job, recruiter: RecruiterProfile) -> tuple[JobAssignment, bool]:
    """Put the recruiter on the job (again, if they left), with their chat channel.

    Under the job lock, so `assign_recruiters` and `unassign_recruiter` see the assignment.
    """
    locked = _locked(job)
    assignment, created = JobAssignment.objects.get_or_create(job=locked, recruiter=recruiter)
    if not created and assignment.left_at is not None:
        assignment.left_at = None
        assignment.save(update_fields=["left_at"])
    ensure_channel(locked, recruiter)
    return assignment, created


@transaction.atomic
def join_marketplace_job(
    job_id: UUID | str, recruiter: RecruiterProfile
) -> tuple[JobAssignment, bool, Channel]:
    """Join from the marketplace: only a job that is on it, checked while holding its lock.

    The lookup is the lock (`FOR UPDATE OF` the job row), and Postgres re-reads the row's
    conditions once it gets the lock, so a close or marketplace-off that commits while this
    waits is seen and the join is a 404, not an assignment on a job that just left.
    """
    locked = (
        marketplace_queryset(recruiter).select_for_update(of=("self",)).filter(pk=job_id).first()
    )
    if locked is None:
        raise Http404
    assignment, created = join_job(locked, recruiter)
    # A plain read of the general thread, not a second `ensure_channel`: `join_job` has just made
    # sure it exists. A candidate thread of theirs may exist too, from a CV they sent before they
    # left, so the lookup must say which one it means.
    channel = Channel.objects.get(job=locked, recruiter=recruiter, application__isnull=True)
    return assignment, created, channel
