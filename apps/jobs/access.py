"""Who may see a job and what they may do to it (spec §4).

`jobs_visible_to` / `can_view_job` are the one scoping rule for a job and everything that
hangs off it: applications, candidate detail, pipeline, talent pool, chat, exports,
approvals, the company home and the counts.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from django.db.models import Exists, OuterRef, Q, QuerySet

from apps.accounts.models import User
from apps.candidates.models import Stage
from apps.companies.access import membership_of
from apps.companies.models import CompanyRole
from apps.jobs.bounty import HAS_BOUNTY, has_bounty
from apps.jobs.models import (
    PRIVATE_STATUSES,
    InviteStatus,
    Job,
    JobAssignment,
    JobInvite,
    JobStatus,
)

if TYPE_CHECKING:
    from apps.recruiters.models import RecruiterProfile

# Shown when a member can see the job but this action is not theirs to take.
NOT_YOURS = "You can't do that on this job."

# Candidates still in play: a job with any of these may not be deleted.
LIVE_STAGES = tuple(stage for stage in Stage if stage not in (Stage.ONBOARDED, Stage.REJECTED))

# A Talent Acquisition member without posting rights proposes changes to these.
PROPOSABLE_STATUSES = (JobStatus.PENDING, JobStatus.ACTIVE, JobStatus.CLOSED)


def has_live_applications(job: Job) -> bool:
    return job.applications.filter(stage__in=LIVE_STAGES).exists()


def jobs_visible_to(user: User | None) -> QuerySet[Job]:
    """An admin sees every team's jobs; anyone else only their own team's.

    Nobody sees a deleted job. An unsent draft is its creator's alone, and a job waiting for
    the admin is also hidden from the creator's teammates.
    """
    membership = membership_of(user)
    if user is None or membership is None:
        return Job.objects.none()
    jobs = Job.objects.filter(company_id=membership.company_id, deleted_at__isnull=True)
    if membership.role == CompanyRole.ADMIN:
        private: tuple[str, ...] = (JobStatus.DRAFT,)
    elif membership.team_id is None:
        return Job.objects.none()
    else:
        jobs = jobs.filter(team_id=membership.team_id)
        private = PRIVATE_STATUSES
    return jobs.filter(~Q(status__in=private) | Q(created_by_id=user.pk))


def can_view_job(user: User | None, job: Job) -> bool:
    """`jobs_visible_to` for one row, without a query."""
    membership = membership_of(user)
    if user is None or membership is None:
        return False
    if membership.company_id != job.company_id or job.deleted_at is not None:
        return False
    own = job.created_by_id == user.pk
    if membership.role == CompanyRole.ADMIN:
        return own or job.status != JobStatus.DRAFT
    if membership.team_id is None or job.team_id != membership.team_id:
        return False
    return own or job.status not in PRIVATE_STATUSES


def can_create_jobs(user: User | None) -> bool:
    membership = membership_of(user)
    return membership is not None and membership.role in (
        CompanyRole.ADMIN,
        CompanyRole.TALENT_ACQUISITION,
    )


@dataclass(frozen=True)
class JobPermissions:
    edit: bool
    submit: bool
    withdraw: bool
    post: bool
    close: bool
    reopen: bool
    delete: bool
    propose: bool
    review_proposals: bool

    NONE: ClassVar["JobPermissions"]


JobPermissions.NONE = JobPermissions(*[False] * 9)


def job_permissions(user: User | None, job: Job) -> JobPermissions:
    """Spec §4. "Own" means the user created the job."""
    if user is None or not can_view_job(user, job):
        return JobPermissions.NONE
    membership = membership_of(user)
    assert membership is not None, "can_view_job only passes members"
    status = job.status
    own = job.created_by_id == user.pk

    def deletable_once_closed() -> bool:
        return status == JobStatus.CLOSED and not has_live_applications(job)

    if membership.role == CompanyRole.ADMIN:
        return JobPermissions(
            edit=True,
            submit=False,
            withdraw=False,
            post=status in PRIVATE_STATUSES,
            close=status == JobStatus.ACTIVE,
            reopen=status == JobStatus.CLOSED,
            delete=(status == JobStatus.DRAFT and own) or deletable_once_closed(),
            propose=False,
            review_proposals=True,
        )
    if membership.role != CompanyRole.TALENT_ACQUISITION or not own:
        return JobPermissions.NONE
    if membership.can_publish:
        # Ruling R7: a job sent for approval before posting rights came can be posted now.
        return JobPermissions(
            edit=True,
            submit=False,
            withdraw=False,
            post=status in PRIVATE_STATUSES,
            close=status == JobStatus.ACTIVE,
            reopen=status == JobStatus.CLOSED,
            delete=status == JobStatus.DRAFT or deletable_once_closed(),
            propose=False,
            review_proposals=False,
        )
    return JobPermissions(
        edit=status in PRIVATE_STATUSES,
        submit=status == JobStatus.DRAFT,
        withdraw=status == JobStatus.SUBMITTED,
        post=False,
        close=False,
        reopen=False,
        delete=status == JobStatus.DRAFT,
        propose=status in PROPOSABLE_STATUSES,
        review_proposals=False,
    )


def can_change_job(user: User | None, job: Job) -> bool:
    return job_permissions(user, job).edit


def live_jobs() -> QuerySet[Job]:
    """What a headhunter can ever see: active, not deleted, and with a bounty set (spec §9)."""
    return Job.objects.filter(HAS_BOUNTY, status=JobStatus.ACTIVE, deleted_at__isnull=True)


def jobs_offered_to(recruiter: "RecruiterProfile | None") -> QuerySet[Job]:
    """The live jobs `recruiter` may open: on the marketplace, invited to, or already on.

    A subquery per channel rather than a join, so a job reachable twice is still one row.
    """
    if recruiter is None:
        return Job.objects.none()
    invited = JobInvite.objects.filter(
        job=OuterRef("pk"), recruiter=recruiter, status=InviteStatus.PENDING
    )
    working = JobAssignment.objects.filter(
        job=OuterRef("pk"), recruiter=recruiter, left_at__isnull=True
    )
    return live_jobs().filter(Q(on_marketplace=True) | Exists(invited) | Exists(working))


def jobs_joined_by(recruiter: "RecruiterProfile | None") -> QuerySet[Job]:
    """Every job `recruiter` is on, closed and deleted too, so their CVs and commission stay."""
    if recruiter is None:
        return Job.objects.none()
    return Job.objects.filter(
        assignments__recruiter=recruiter, assignments__left_at__isnull=True
    ).distinct()


def can_submit_to_job(recruiter: "RecruiterProfile | None", job: Job) -> bool:
    """A live job the recruiter is on; the marketplace being off does not matter."""
    if (
        recruiter is None
        or job.status != JobStatus.ACTIVE
        or job.deleted_at is not None
        or not has_bounty(job)
    ):
        return False
    return JobAssignment.objects.filter(job=job, recruiter=recruiter, left_at__isnull=True).exists()
