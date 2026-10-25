from typing import Any

from django.db.models import Count, F, Q

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.stages import STAGE_ORDER
from apps.jobs.access import job_permissions, jobs_visible_to
from apps.jobs.models import ChangeKind, ChangeStatus, Job, JobChangeRequest, JobStatus
from apps.jobs.proposals import change_requests_visible_to, is_admin
from apps.jobs.queries import annotate_job_metrics
from apps.jobs.questions import unanswered_counts

MAX_ACTIONS = 5
PIPELINE_STAGES = [*STAGE_ORDER, Stage.REJECTED]
JOB_STATUSES = list(JobStatus)
# The row's second line names the job, so the first says what was proposed for it.
PROPOSAL_TITLES = {
    ChangeKind.EDIT: "{name} proposed changes to this job",
    ChangeKind.CLOSE: "{name} proposed closing this job",
    ChangeKind.REOPEN: "{name} proposed reopening this job",
    ChangeKind.DELETE: "{name} proposed deleting this job",
}


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
    # Counted from the plain visible set: `queryset` carries the metric annotations.
    unanswered = unanswered_counts(jobs_visible_to(user).filter(status=JobStatus.ACTIVE))
    return [
        {
            "id": str(job.pk),
            "title": job.title,
            "current_stage": _stage_of(job),
            "days_open": getattr(job, "days_open", 0) or 0,
            "cv_count": getattr(job, "cv_count", 0) or 0,
            "new_count": getattr(job, "new_count", 0) or 0,
            "unanswered_questions": unanswered.get(job.pk, 0),
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
    # Whoever may post it is asked to; the creator who may not is told who is holding it.
    submitted = [
        {
            "kind": "job_submitted",
            "title": job.title,
            "detail": "Waiting for you to post" if can_post else "Waiting for your company admin",
            "href": f"/jobs/{job.pk}/edit" if can_post else f"/jobs/{job.pk}",
        }
        for job in visible.filter(status=JobStatus.SUBMITTED).order_by("-created_at")
        for can_post in [job_permissions(user, job).post]
    ]
    proposals: list[JobChangeRequest] = (
        list(
            change_requests_visible_to(user)
            .filter(status=ChangeStatus.PENDING)
            .select_related("requested_by", "job")
            .order_by("created_at")
        )
        if is_admin(user)
        else []
    )
    changes = [
        {
            "kind": "change_request",
            "title": PROPOSAL_TITLES[ChangeKind(proposal.kind)].format(
                name=proposal.requested_by.full_name or proposal.requested_by.email
            ),
            "detail": proposal.job.title,
            "href": f"/jobs/{proposal.job_id}",
        }
        for proposal in proposals
    ]
    waiting = [
        {
            "kind": "job_pending",
            "title": job.title,
            "detail": "Waiting for Sparktal",
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
    return [*submitted, *changes, *waiting, *reviews][:MAX_ACTIONS]


def home_payload(user: User) -> dict[str, Any]:
    return {
        "jobs": job_counts(user),
        "pipeline": pipeline_counts(user),
        "active_jobs": active_jobs(user),
        "actions": actions(user),
    }
