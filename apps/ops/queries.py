from typing import Any

from django.db.models import Count, IntegerField, OuterRef, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce

from apps.companies.models import Company, CompanyMembership, Team
from apps.jobs.models import InviteStatus, Job, JobAssignment, JobInvite, JobStatus
from apps.recruiters.models import RecruiterProfile


def _count(rows: QuerySet[Any], by: str = "job") -> Coalesce:
    """How many of `rows`, which are tied to the outer row by `OuterRef` on their `by` field;
    0 when there are none."""
    counted = rows.order_by().values(by).annotate(total=Count("pk")).values("total")
    return Coalesce(Subquery(counted, output_field=IntegerField()), Value(0))


def annotate_ops_jobs(queryset: QuerySet[Job]) -> QuerySet[Job]:
    """What `OpsJobListItemSerializer` reads, in one query however many jobs there are.

    Never serialize a `Job` with it that did not come through here. Each number is a correlated
    subquery, not a join: counting invites and assignments through two joins would build an
    invites by assignments product for every job.
    """
    invites = JobInvite.objects.filter(job=OuterRef("pk"))
    return queryset.select_related("company", "team").annotate(
        invites_pending=_count(invites.filter(status=InviteStatus.PENDING)),
        invites_accepted=_count(invites.filter(status=InviteStatus.ACCEPTED)),
        invites_declined=_count(invites.filter(status=InviteStatus.DECLINED)),
        recruiters_working=_count(
            JobAssignment.objects.filter(job=OuterRef("pk"), left_at__isnull=True)
        ),
    )


def annotate_ops_companies(queryset: QuerySet[Company]) -> QuerySet[Company]:
    """What `OpsCompanySerializer` reads, in one query however many companies there are.

    Correlated subqueries again, for the same reason as `annotate_ops_jobs`. A soft-deleted job
    counts nowhere, whatever its status says.
    """
    jobs = Job.objects.filter(company=OuterRef("pk"), deleted_at__isnull=True)
    return queryset.annotate(
        member_count=_count(CompanyMembership.objects.filter(company=OuterRef("pk")), "company"),
        team_count=_count(Team.objects.filter(company=OuterRef("pk")), "company"),
        active_job_count=_count(jobs.filter(status=JobStatus.ACTIVE), "company"),
        queued_job_count=_count(jobs.filter(status=JobStatus.PENDING), "company"),
    )


def annotate_ops_recruiters(queryset: QuerySet[RecruiterProfile]) -> QuerySet[RecruiterProfile]:
    """What `OpsRecruiterSerializer` reads, in one query however many recruiters there are.

    `active_job_count` is the jobs the recruiter works now: an assignment they have not left, on
    a job that is live and not deleted. Another correlated subquery, as above.
    """
    working = JobAssignment.objects.filter(
        recruiter=OuterRef("pk"),
        left_at__isnull=True,
        job__status=JobStatus.ACTIVE,
        job__deleted_at__isnull=True,
    )
    return queryset.select_related("user").annotate(active_job_count=_count(working, "recruiter"))
