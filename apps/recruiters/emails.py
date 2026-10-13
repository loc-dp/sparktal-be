from django.conf import settings

from apps.core.emails import build_email
from apps.recruiters.models import RecruiterProfile, RecruiterStatus


def send_review_result(profile: RecruiterProfile) -> None:
    approved = profile.status == RecruiterStatus.APPROVED
    build_email(
        "email/recruiter_approved" if approved else "email/recruiter_rejected",
        profile.user.email,
        {
            "nickname": profile.nickname,
            "review_note": profile.review_note,
            "jobs_url": f"{settings.FRONTEND_URL}/r/chat",
        },
    ).send()
