from typing import Any

from django.db.models import Count, F, Q

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.stages import STAGE_ORDER
from apps.jobs.access import jobs_visible_to
from apps.jobs.models import Job, JobStatus
from apps.jobs.queries import annotate_job_metrics

MAX_ACTIONS = 5
PIPELINE_STAGES = [*STAGE_ORDER, Stage.REJECTED]
JOB_STATUSES = [JobStatus.PENDING, JobStatus.ACTIVE, JobStatus.CLOSED]


def job_counts(user: User) -> dict[str, int]:
    counts: dict[str, int] = {str(status): 0 for status in JOB_STATUSES}
    for row in jobs_visible_to(user).values("status").annotate(total=Count("id")):
        counts[row["status"]] = row["total"]
    return counts


def pipeline_counts(user: User) -> dict[str, int]:
    counts: dict[str, int] = {str(stage): 0 for stage in PIPELINE_STAGES}
    rows = (
        Application.objects.filter(job__in=jobs_visible_to(user))
        .values("stage")
        .annotate(total=Count("id"))
    )
    for row in rows:
        if row["stage"] in counts:
            counts[row["stage"]] = row["total"]
    return counts


def active_jobs(user: User) -> list[dict[str, Any]]:
    queryset = annotate_job_metrics(jobs_visible_to(user).filter(status=JobStatus.ACTIVE)).order_by(
        F("new_count").desc(), F("cv_count").desc(), "title"
    )
    return [
        {
            "id": str(job.pk),
            "title": job.title,
            "current_stage": _stage_of(job),
            "days_open": getattr(job, "days_open", 0) or 0,
            "cv_count": getattr(job, "cv_count", 0) or 0,
            "new_count": getattr(job, "new_count", 0) or 0,
        }
        for job in queryset
    ]


def _stage_of(job: Job) -> str | None:
    rank = getattr(job, "stage_rank", None)
    if rank is None or rank < 0:
        return None
    return str(STAGE_ORDER[rank])


def _review_detail(count: int) -> str:
    return f"{count} new CV{'s' if count > 1 else ''} to review"


def actions(user: User) -> list[dict[str, Any]]:
    visible = jobs_visible_to(user)
    waiting = [
        {
            "kind": "job_pending",
            "title": job.title,
            "detail": "Waiting for Sparktal to approve",
            "href": f"/jobs/{job.pk}",
        }
        for job in visible.filter(status=JobStatus.PENDING).order_by("-created_at")
    ]
    busy = (
        visible.filter(status=JobStatus.ACTIVE)
        .annotate(new_count=Count("applications", filter=Q(applications__stage=Stage.NEW)))
        .filter(new_count__gt=0)
        .order_by(F("new_count").desc(), "title")
    )
    reviews = [
        {
            "kind": "new_applicants",
            "title": job.title,
            "detail": _review_detail(getattr(job, "new_count", 0)),
            "href": f"/jobs/{job.pk}/applicants",
        }
        for job in busy
    ]
    return [*waiting, *reviews][:MAX_ACTIONS]


def home_payload(user: User) -> dict[str, Any]:
    return {
        "jobs": job_counts(user),
        "pipeline": pipeline_counts(user),
        "active_jobs": active_jobs(user),
        "actions": actions(user),
    }
