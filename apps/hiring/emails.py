"""What recruiters hear about a hire after the offer: it fell through, or Sparktal paid them.

Same pattern as `apps.jobs.notifications`: a failure is logged and never reaches the request.
"""

import logging

from django.conf import settings

from apps.core.emails import build_email
from apps.hiring.models import Placement, PlacementOutcome
from apps.jobs.notifications import _vnd, send_safely

logger = logging.getLogger(__name__)


def _earnings_url() -> str:
    return f"{settings.FRONTEND_URL}/r/earnings"


def notify_recruiter_outcome(placement: Placement) -> None:
    """Tell the recruiter their hire never started or left during the guarantee."""
    try:
        application = placement.application
        recruiter = application.recruiter.user
        if not recruiter.is_active:
            return
        context = {
            "candidate_name": application.candidate.full_name,
            "job_title": application.job.title,
            "company_name": application.job.company.name,
            "no_show": placement.outcome == PlacementOutcome.NO_SHOW,
            "ended_on": placement.ended_on,
            "note": placement.outcome_note,
            "earnings_url": _earnings_url(),
        }
        message = build_email("email/placement_ended", recruiter.email, context)
    except Exception:
        logger.exception("Could not prepare the placement ended email for %s", placement.pk)
        return
    send_safely(message)


def notify_recruiter_paid(placement: Placement) -> None:
    """Tell the recruiter Sparktal paid them for a hire, with the amount and the date."""
    try:
        application = placement.application
        recruiter = application.recruiter.user
        if not recruiter.is_active or placement.paid_amount is None:
            return
        context = {
            "candidate_name": application.candidate.full_name,
            "job_title": application.job.title,
            "company_name": application.job.company.name,
            "amount": _vnd(placement.paid_amount),
            "paid_at": placement.paid_at,
            "note": placement.payout_note,
            "earnings_url": _earnings_url(),
        }
        message = build_email("email/placement_paid", recruiter.email, context)
    except Exception:
        logger.exception("Could not prepare the placement paid email for %s", placement.pk)
        return
    send_safely(message)
