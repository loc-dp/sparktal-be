import logging
from collections.abc import Sequence
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
from django.core.mail import EmailMultiAlternatives
from django.utils.text import capfirst

from apps.accounts.models import User
from apps.companies.models import CompanyMembership, CompanyRole
from apps.core.emails import build_email
from apps.jobs.access import can_change_job
from apps.jobs.models import (
    BountyType,
    ChangeKind,
    ChangeStatus,
    Job,
    JobChangeRequest,
    JobInvite,
    JobQuestion,
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


def notify_recruiter_invited(invite: JobInvite) -> None:
    """Tell a recruiter Sparktal offered them a job, with its bounty as the marketplace shows it."""
    try:
        job = invite.job
        recruiter = invite.recruiter
        context = {
            "job_title": job.title,
            "company_name": job.company.name,
            "nickname": recruiter.nickname,
            "bounty_type": job.bounty_type,
            "bounty_pct": job.bounty_pct,
            "bounty_amount": _vnd(job.bounty_amount)
            if job.bounty_type == BountyType.FIXED and job.bounty_amount is not None
            else None,
            "job_url": f"{settings.FRONTEND_URL}/r/marketplace/{job.pk}",
        }
        message = build_email("email/recruiter_invited", recruiter.user.email, context)
    except Exception:
        logger.exception("Could not prepare the invite email for %s", invite.pk)
        return
    send_safely(message)


def notify_question_asked(question: JobQuestion) -> None:
    """Tell the company's active admins, and the creator if they may edit the job, of a question.

    Only people who can answer hear about it; a creator without edit rights cannot.
    """
    try:
        job = question.job
        recipients = {
            membership.user.pk: membership.user
            for membership in CompanyMembership.objects.filter(
                company_id=job.company_id, role=CompanyRole.ADMIN, user__is_active=True
            ).select_related("user")
        }
        creator = job.created_by
        if creator is not None and creator.is_active and can_change_job(creator, job):
            recipients[creator.pk] = creator
        context = {
            "job_title": job.title,
            "recruiter_name": question.asked_by.nickname,
            "question": question.body,
            "job_url": f"{settings.FRONTEND_URL}/jobs/{job.pk}#questions",
        }
    except Exception:
        logger.exception("Could not prepare the question asked email for %s", question.pk)
        return
    for user in recipients.values():
        try:
            message = build_email("email/question_asked", user.email, context)
        except Exception:
            logger.exception("Could not build the question asked email for %s", user)
            continue
        send_safely(message)


def notify_question_answered(question: JobQuestion) -> None:
    """Tell the recruiter who asked that the company answered."""
    try:
        job = question.job
        asker = question.asked_by.user
        if not asker.is_active:
            return
        context = {
            "job_title": job.title,
            "company_name": job.company.name,
            "question": question.body,
            "answer": question.answer,
            "job_url": f"{settings.FRONTEND_URL}/r/jobs/{job.pk}",
        }
        message = build_email("email/question_answered", asker.email, context)
    except Exception:
        logger.exception("Could not prepare the question answered email for %s", question.pk)
        return
    send_safely(message)
