from typing import Any

from django.db.models import Count, F, Q

from apps.candidates.models import Application, Stage
from apps.companies.models import Company
from apps.jobs.models import Job, JobStatus
from apps.ops.queries import annotate_ops_jobs
from apps.recruiters.models import RecruiterProfile, RecruiterStatus

QUEUE_SIZE = 5


def ops_overview() -> dict[str, Any]:
    """The Sparktal dashboard: platform totals and the oldest jobs waiting in the queue.

    A queued job is one the company posted (`pending`) that waits for Sparktal to set a bounty
    and distribute it.
    """
    jobs = Job.objects.filter(deleted_at__isnull=True)
    counts = jobs.aggregate(
        active_jobs=Count("id", filter=Q(status=JobStatus.ACTIVE)),
        queued_jobs=Count("id", filter=Q(status=JobStatus.PENDING)),
    )
    cvs = Application.objects.aggregate(
        cvs_submitted=Count("id"), hires=Count("id", filter=Q(stage=Stage.ONBOARDED))
    )
    waiting = annotate_ops_jobs(jobs.filter(status=JobStatus.PENDING))
    return {
        "companies": Company.objects.count(),
        **counts,
        "pending_recruiters": RecruiterProfile.objects.filter(
            status=RecruiterStatus.PENDING
        ).count(),
        **cvs,
        "queue": list(waiting.order_by(F("posted_at").asc(nulls_last=True), "id")[:QUEUE_SIZE]),
    }
