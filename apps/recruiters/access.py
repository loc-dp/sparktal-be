from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from apps.accounts.models import User
from apps.recruiters.models import RecruiterProfile, RecruiterStatus

WAITING_MESSAGE = "Your recruiter profile is waiting for review."
NOT_A_RECRUITER_MESSAGE = "This area is for recruiter accounts."


def recruiter_profile_of(user: User | Any) -> RecruiterProfile | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "recruiter", None)


def approved_recruiter(user: User | Any) -> RecruiterProfile | None:
    profile = recruiter_profile_of(user)
    if profile is None or profile.status != RecruiterStatus.APPROVED:
        return None
    return profile


class IsApprovedRecruiter(BasePermission):
    message = WAITING_MESSAGE

    def has_permission(self, request: Request, view: Any) -> bool:
        # A company user is not a recruiter waiting on us, so say the right thing.
        self.message = (
            WAITING_MESSAGE if recruiter_profile_of(request.user) else NOT_A_RECRUITER_MESSAGE
        )
        return approved_recruiter(request.user) is not None
