from typing import TYPE_CHECKING

from django.db.models import QuerySet
from guardian.shortcuts import assign_perm, get_objects_for_user

from apps.accounts.models import User
from apps.companies.access import membership_of
from apps.companies.models import CompanyRole
from apps.jobs.models import Job, JobAssignment, JobStatus

if TYPE_CHECKING:
    from apps.recruiters.models import RecruiterProfile

FULL_ACCESS_ROLES = {CompanyRole.ADMIN, CompanyRole.TALENT_ACQUISITION}


def jobs_visible_to(user: User | None) -> QuerySet[Job]:
    """Admin/TA see every job of their company; a Hiring Manager only granted ones."""
    membership = membership_of(user)
    if membership is None:
        return Job.objects.none()
    company_jobs = Job.objects.filter(company_id=membership.company_id)
    if membership.role in FULL_ACCESS_ROLES:
        return company_jobs
    granted: QuerySet[Job] = get_objects_for_user(
        user, "jobs.view_job", klass=company_jobs, accept_global_perms=False
    )
    return granted


def can_view_job(user: User | None, job: Job) -> bool:
    membership = membership_of(user)
    if membership is None or membership.company_id != job.company_id:
        return False
    if membership.role in FULL_ACCESS_ROLES:
        return True
    return user is not None and user.has_perm("jobs.view_job", job)


def can_change_job(user: User | None, job: Job) -> bool:
    membership = membership_of(user)
    if membership is None or membership.company_id != job.company_id:
        return False
    if membership.role in FULL_ACCESS_ROLES:
        return True
    return user is not None and user.has_perm("jobs.change_job", job)


def grant_creator_perms(job: Job, user: User) -> None:
    """A Hiring Manager keeps access to the jobs they open."""
    assign_perm("jobs.view_job", user, job)
    assign_perm("jobs.change_job", user, job)


def jobs_joined_by(recruiter: "RecruiterProfile | None") -> QuerySet[Job]:
    if recruiter is None:
        return Job.objects.none()
    return Job.objects.filter(
        assignments__recruiter=recruiter, assignments__left_at__isnull=True
    ).distinct()


def can_submit_to_job(recruiter: "RecruiterProfile | None", job: Job) -> bool:
    if recruiter is None or job.status != JobStatus.ACTIVE:
        return False
    return JobAssignment.objects.filter(job=job, recruiter=recruiter, left_at__isnull=True).exists()
