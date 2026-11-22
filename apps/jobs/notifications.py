import logging
from collections.abc import Sequence
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.core.mail import EmailMultiAlternatives
from django.utils.text import capfirst

from apps.accounts.models import User, UserKind
from apps.companies.models import CompanyMembership, CompanyRole
from apps.core.emails import build_email
from apps.jobs.access import can_change_job
from apps.jobs.models import (
    BountyType,
    ChangeKind,
    ChangeStatus,
    Job,
    JobAssignment,
    JobChangeRequest,
    JobComment,
)

logger = logging.getLogger(__name__)


def send_safely(message: EmailMultiAlternatives) -> None:
    """Send `message`; a failure is logged and never raised, so it cannot break a request."""
    try:
        message.send()
    except Exception:
        logger.exception("Could not send email %r to %s", message.subject, message.to)


def notify_admins_job_submitted(job: Job) -> None:
    """Tell every active admin of the company that `job` waits for them to post."""
    try:
        requester = job.created_by
        admins = list(
            CompanyMembership.objects.filter(
                company_id=job.company_id, role=CompanyRole.ADMIN, user__is_active=True
            ).select_related("user")
        )
        context = {
            "job_title": job.title,
            "requester_name": (
                (requester.full_name or requester.email) if requester else "A teammate"
            ),
            "job_url": f"{settings.FRONTEND_URL}/jobs/{job.pk}/edit",
        }
    except Exception:
        logger.exception("Could not prepare the job submitted email for job %s", job.pk)
        return
    for membership in admins:
        try:
            message = build_email("email/job_submitted", membership.user.email, context)
        except Exception:
            logger.exception("Could not build the job submitted email for %s", membership.user)
            continue
        send_safely(message)


def _person(user: User) -> str:
    return str(user.full_name or user.email)


def notify_admins_change_requested(change: JobChangeRequest) -> None:
    """Tell every active admin of the company that a member proposed a change."""
    try:
        job = change.job
        context = {
            "job_title": job.title,
            "requester_name": _person(change.requested_by),
            "kind_label": change.get_kind_display().lower(),
            "job_url": f"{settings.FRONTEND_URL}/jobs/{job.pk}",
        }
        admins = list(
            CompanyMembership.objects.filter(
                company_id=job.company_id, role=CompanyRole.ADMIN, user__is_active=True
            ).select_related("user")
        )
    except Exception:
        logger.exception("Could not prepare the change requested email for %s", change.pk)
        return
    for membership in admins:
        try:
            message = build_email("email/change_requested", membership.user.email, context)
        except Exception:
            logger.exception("Could not build the change requested email for %s", membership.user)
            continue
        send_safely(message)


def notify_requester_change_decided(change: JobChangeRequest) -> None:
    """Tell the requester their proposal was approved or rejected, with the admin's note."""
    try:
        requester = change.requested_by
        if not requester.is_active:
            return
        context = {
            "job_title": change.job.title,
            "kind_label": change.get_kind_display().lower(),
            "decision": change.get_status_display().lower(),
            "note": change.review_note,
            # A deleted job has no page to go to.
            "job_url": f"{settings.FRONTEND_URL}/jobs"
            if change.kind == ChangeKind.DELETE and change.status == ChangeStatus.APPROVED
            else f"{settings.FRONTEND_URL}/jobs/{change.job_id}",
        }
        message = build_email("email/change_decided", requester.email, context)
    except Exception:
        logger.exception("Could not prepare the change decided email for %s", change.pk)
        return
    send_safely(message)


def ops_job_url(job: Job) -> str:
    """Where Sparktal ops handle `job`: its page in the ops portal."""
    return f"{settings.FRONTEND_URL}/a/jobs/{job.pk}"


def _field_label(name: str) -> str:
    """`salary_max` reads "Salary max", the model field's own name where there is one."""
    try:
        field = Job._meta.get_field(name)
    except FieldDoesNotExist:
        field = None
    return str(capfirst(getattr(field, "verbose_name", None) or name.replace("_", " ")))


def _notify_ops(template_prefix: str, job: Job, **context: object) -> None:
    """Email SPARKTAL_OPS_EMAIL about `job`; any failure is logged, never raised."""
    if not settings.SPARKTAL_OPS_EMAIL:
        return
    try:
        message = build_email(
            template_prefix,
            settings.SPARKTAL_OPS_EMAIL,
            {
                "job_title": job.title,
                "company_name": job.company.name,
                "job_url": ops_job_url(job),
                **context,
            },
        )
    except Exception:
        logger.exception("Could not build the %s email for job %s", template_prefix, job.pk)
        return
    send_safely(message)


def notify_ops_job_queued(job: Job) -> None:
    """Tell Sparktal a job waits for its bounty and channels (spec §10)."""
    _notify_ops("email/ops_job_queued", job)


def notify_ops_job_edited(job: Job, fields: Sequence[str]) -> None:
    """Tell Sparktal the company changed a live job, naming the fields it changed."""
    _notify_ops("email/ops_job_edited", job, fields=[_field_label(name) for name in fields])


def _vnd(amount: Decimal) -> str:
    """`120.000.000`, as the marketplace writes VND."""
    return f"{int(amount):,}".replace(",", ".")


def notify_recruiter_assigned(assignment: JobAssignment) -> None:
    """Tell a recruiter Sparktal assigned them a job, with its bounty as they will see it."""
    try:
        job = assignment.job
        recruiter = assignment.recruiter
        context = {
            "job_title": job.title,
            "company_name": job.company.name,
            "nickname": recruiter.nickname,
            "bounty_type": job.bounty_type,
            "bounty_pct": job.bounty_pct,
            "bounty_amount": _vnd(job.bounty_amount)
            if job.bounty_type == BountyType.FIXED and job.bounty_amount is not None
            else None,
            "job_url": f"{settings.FRONTEND_URL}/r/jobs/{job.pk}",
        }
        message = build_email("email/recruiter_assigned", recruiter.user.email, context)
    except Exception:
        logger.exception("Could not prepare the assigned email for %s", assignment.pk)
        return
    send_safely(message)


ANOTHER_HEADHUNTER = "Another headhunter"


def _comment_recipients(comment: JobComment) -> list[User]:
    """Who a comment concerns (spec §5.3), before the read check and the de-duplication."""
    job = comment.job
    if comment.parent is not None:
        # A reply tells the one it answers.
        return [comment.parent.author] if comment.parent.author is not None else []
    if comment.from_recruiter:
        # The people `can_change_job` lets answer: every admin, and the creator when they may edit.
        recipients = [
            membership.user
            for membership in CompanyMembership.objects.filter(
                company_id=job.company_id, role=CompanyRole.ADMIN
            ).select_related("user")
        ]
        creator = job.created_by
        if creator is not None and can_change_job(creator, job):
            recipients.append(creator)
        return recipients
    return [
        assignment.recruiter.user
        for assignment in JobAssignment.objects.filter(
            job=job, left_at__isnull=True
        ).select_related("recruiter__user")
    ]


def _comment_label(comment: JobComment, viewer: User, company_name: str) -> str:
    """The author as `viewer` may be told; never another headhunter's nickname or email."""
    # `comments` imports this module to queue the mail, so it is imported when needed.
    from apps.jobs.comments import author_name

    name = author_name(comment, viewer, company_name)
    if name is not None:
        return name
    return (
        ANOTHER_HEADHUNTER if comment.from_recruiter and comment.author is not None else "Someone"
    )


def notify_comment_posted(comment: JobComment) -> None:
    """Tell the people a new comment concerns (spec §5.3); runs after the comment's commit.

    A headhunter's top-level comment goes to the company's admins and the job's editing
    creator; a company comment to the headhunters holding the job; a reply to the author of
    the comment it answers. Nobody is told of their own comment, nobody twice, and nobody who
    cannot read the thread now. Never raises.
    """
    try:
        from apps.jobs.comments import can_read_comments

        fresh = (
            JobComment.objects.select_related(
                "job__company", "job__created_by", "author__recruiter", "parent__author"
            )
            .filter(pk=comment.pk, hidden_at__isnull=True, deleted_at__isnull=True)
            .first()
        )
        if fresh is None:
            return
        parent = fresh.parent
        if parent is not None and (parent.hidden_at is not None or parent.deleted_at is not None):
            return
        job = fresh.job
        template = (
            "email/comment_replied"
            if parent is not None
            else "email/comment_posted"
            if fresh.from_recruiter
            else "email/comment_from_company"
        )
        recipients = _comment_recipients(fresh)
    except Exception:
        logger.exception("Could not prepare the comment email for %s", comment.pk)
        return
    told: set[str] = set()
    for user in recipients:
        try:
            address = (user.email or "").strip().lower()
            if (
                not address
                or not user.is_active
                or user.pk == fresh.author_id
                or address in told
                or not can_read_comments(user, job)
            ):
                continue
            company_name = str(job.company.name)
            for_headhunter = user.kind == UserKind.RECRUITER
            message = build_email(
                template,
                user.email,
                {
                    "job_title": job.title,
                    "company_name": company_name,
                    "author_label": _comment_label(fresh, user, company_name),
                    "body": fresh.body,
                    "parent_body": parent.body if parent is not None else "",
                    "job_url": f"{settings.FRONTEND_URL}/r/jobs/{job.pk}/details#questions"
                    if for_headhunter
                    else f"{settings.FRONTEND_URL}/jobs/{job.pk}#questions",
                },
            )
        except Exception:
            logger.exception("Could not build the comment email for %s", user.pk)
            continue
        told.add(address)
        send_safely(message)
