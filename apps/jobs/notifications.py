import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from apps.accounts.models import User
from apps.companies.models import CompanyMembership, CompanyRole
from apps.core.emails import build_email
from apps.jobs.models import ChangeKind, ChangeStatus, Job, JobChangeRequest

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
