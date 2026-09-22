from django.conf import settings
from django.core.mail import send_mail

from apps.recruiters.models import RecruiterProfile, RecruiterStatus

APPROVED_SUBJECT = "Your Sparktal recruiter account is approved"
REJECTED_SUBJECT = "About your Sparktal recruiter application"


def send_review_result(profile: RecruiterProfile) -> None:
    if profile.status == RecruiterStatus.APPROVED:
        subject = APPROVED_SUBJECT
        body = (
            f"Hello {profile.nickname},\n\n"
            "Your recruiter account is live. Sign in to browse the jobs marketplace "
            f"at {settings.FRONTEND_URL}/r/chat.\n"
        )
    else:
        subject = REJECTED_SUBJECT
        body = (
            f"Hello {profile.nickname},\n\n"
            "We could not approve your recruiter account this time.\n\n"
            f"{profile.review_note}\n"
        )
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [profile.user.email])
