import logging

from django.conf import settings

from apps.core.emails import build_email
from apps.jobs.notifications import send_safely
from apps.recruiters.models import RecruiterProfile, RecruiterStatus

logger = logging.getLogger(__name__)


def send_review_result(profile: RecruiterProfile) -> None:
    """Tell a recruiter how Sparktal decided. A failure is logged, never raised: the decision
    is already saved by the time this runs."""
    approved = profile.status == RecruiterStatus.APPROVED
    try:
        message = build_email(
            "email/recruiter_approved" if approved else "email/recruiter_rejected",
            profile.user.email,
            {
                "nickname": profile.nickname,
                "review_note": profile.review_note,
                "jobs_url": f"{settings.FRONTEND_URL}/r/chat",
            },
        )
    except Exception:
        logger.exception("Could not prepare the review result email for %s", profile.pk)
        return
    send_safely(message)
