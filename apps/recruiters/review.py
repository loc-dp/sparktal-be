"""Sparktal's decision on a recruiter's application: approve or reject (spec §10).

The ops portal and the Django admin both decide through here, so the same rules and the same
email apply whichever way Sparktal reviews.
"""

from django.db import transaction
from rest_framework.exceptions import APIException, ValidationError

from apps.accounts.models import User
from apps.recruiters.emails import send_review_result
from apps.recruiters.models import RecruiterProfile, RecruiterStatus

NEEDS_NOTE = "Tell them why."


class ReviewError(APIException):
    status_code = 400
    default_code = "review"
    default_detail = "This recruiter can't be reviewed that way."


def _locked(profile: RecruiterProfile) -> RecruiterProfile:
    """The profile as it is now, locked so two reviews of one recruiter run one after the other.

    Only the profile row is locked (`of=("self",)`), not the joined user's.
    """
    return (
        RecruiterProfile.objects.select_for_update(of=("self",))
        .select_related("user")
        .get(pk=profile.pk)
    )


@transaction.atomic
def approve_recruiter(profile: RecruiterProfile, *, by: User) -> RecruiterProfile:
    """Let a recruiter in. Anyone not yet approved can be; they hear about it once the review
    is saved. Returns the profile as saved."""
    locked = _locked(profile)
    if locked.status == RecruiterStatus.APPROVED:
        raise ReviewError("This recruiter is already approved.")
    locked.approve(by)
    transaction.on_commit(lambda: send_review_result(locked))
    return locked


@transaction.atomic
def reject_recruiter(profile: RecruiterProfile, *, by: User, note: str) -> RecruiterProfile:
    """Turn a recruiter down, with the reason they will read. Returns the profile as saved."""
    note = note.strip()
    if not note:
        raise ValidationError({"note": [NEEDS_NOTE]})
    locked = _locked(profile)
    if locked.status == RecruiterStatus.REJECTED:
        raise ReviewError("This recruiter is already rejected.")
    locked.reject(by, note)
    transaction.on_commit(lambda: send_review_result(locked))
    return locked
