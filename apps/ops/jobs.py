"""What Sparktal staff do to a job: read it, set its bounty and who works it (spec §10).

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
    distribute_job,
    set_bounty,
)
from apps.jobs.models import PRIVATE_STATUSES, Job, JobStatus
from apps.ops.queries import annotate_ops_jobs, ops_job_assignments
from apps.recruiters.models import RecruiterProfile


def posted_jobs() -> QuerySet[Job]:
    """The jobs ops can see: posted by the company, not deleted. A draft is the company's own."""
    return Job.objects.filter(deleted_at__isnull=True).exclude(status__in=PRIVATE_STATUSES)


def ops_jobs() -> QuerySet[Job]:
    """`posted_jobs`, with what `OpsJobListItemSerializer` reads."""
    return annotate_ops_jobs(posted_jobs())


def ops_job_detail_queryset() -> QuerySet[Job]:
    """What `OpsJobDetailSerializer` reads; the one way to fetch a job to return it."""
    return (
        ops_jobs()
        .select_related("office")
        .prefetch_related(Prefetch("assignments", queryset=ops_job_assignments()))
    )


@transaction.atomic
def distribute_with_bounty(
    job_id: UUID,
    *,
    actor: User,
    bounty: dict[str, Any],
    recruiters: Sequence[RecruiterProfile],
    on_marketplace: bool = False,
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
