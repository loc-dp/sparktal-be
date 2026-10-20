"""What Sparktal staff do to a job: read it, and set its bounty and channels (spec §10).

The rules are `apps.jobs.distribution`'s; this module picks which jobs ops can reach and runs
several of those steps as one.
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Prefetch, QuerySet
from django.http import Http404

from apps.accounts.models import User
from apps.jobs.distribution import (
    NOT_WAITING,
    DistributionError,
    cancel_invites,
    distribute_job,
    invite_recruiters,
    set_bounty,
    set_marketplace,
)
from apps.jobs.models import PRIVATE_STATUSES, Job, JobInvite, JobStatus
from apps.ops.queries import annotate_ops_jobs
from apps.recruiters.models import RecruiterProfile


def posted_jobs() -> QuerySet[Job]:
    """The jobs ops can see: posted by the company, not deleted. A draft is the company's own."""
    return Job.objects.filter(deleted_at__isnull=True).exclude(status__in=PRIVATE_STATUSES)


def ops_jobs() -> QuerySet[Job]:
    """`posted_jobs`, with what `OpsJobListItemSerializer` reads."""
    return annotate_ops_jobs(posted_jobs())


def ops_job_detail_queryset() -> QuerySet[Job]:
    """What `OpsJobDetailSerializer` reads; the one way to fetch a job to return it."""
    invites = JobInvite.objects.select_related("recruiter__user").order_by("-created_at", "id")
    return ops_jobs().select_related("office").prefetch_related(Prefetch("invites", invites))


@transaction.atomic
def distribute_with_bounty(
    job_id: UUID,
    *,
    actor: User,
    bounty: dict[str, Any],
    on_marketplace: bool,
    recruiters: Sequence[RecruiterProfile],
) -> Job:
    """Set the bounty, then distribute, as one: a refused distribution leaves the bounty as it was.

    The job is checked first, under its lock, so every way a stale page can go wrong (closed,
    already distributed, deleted, taken back by the company) gets the same sentence instead of
    whichever step happens to notice first.
    """
    job = Job.objects.select_for_update().filter(pk=job_id).first()
    if job is None:
        raise Http404
    if job.status != JobStatus.PENDING or job.deleted_at is not None:
        raise DistributionError(NOT_WAITING)
    set_bounty(job, **bounty)
    return distribute_job(job, actor=actor, on_marketplace=on_marketplace, recruiters=recruiters)


@transaction.atomic
def update_channels(
    job: Job,
    *,
    actor: User,
    on_marketplace: bool | None,
    cancel_invite_ids: Sequence[UUID],
    invite: Sequence[RecruiterProfile],
) -> None:
    """The marketplace switch, invites withdrawn, then new invites, all or none."""
    if on_marketplace is not None:
        set_marketplace(job, on=on_marketplace)
    if cancel_invite_ids:
        cancel_invites(job, invite_ids=cancel_invite_ids)
    if invite:
        invite_recruiters(job, actor=actor, recruiters=invite)
