import pytest
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from apps.accounts.models import User
from apps.recruiters.access import (
    NOT_A_RECRUITER_MESSAGE,
    WAITING_MESSAGE,
    IsApprovedRecruiter,
    approved_recruiter,
    recruiter_profile_of,
)

pytestmark = pytest.mark.django_db


def _request(user: User) -> Request:
    request = Request(APIRequestFactory().get("/"))
    request.user = user
    return request


def test_company_user_has_no_recruiter_profile(company_admin: User) -> None:
    assert recruiter_profile_of(company_admin) is None
    assert approved_recruiter(company_admin) is None


def test_pending_recruiter_is_not_approved(pending_recruiter_user: User) -> None:
    assert recruiter_profile_of(pending_recruiter_user) is not None
    assert approved_recruiter(pending_recruiter_user) is None
    assert IsApprovedRecruiter().has_permission(_request(pending_recruiter_user), None) is False


def test_approved_recruiter_passes_permission(recruiter_user: User) -> None:
    assert IsApprovedRecruiter().has_permission(_request(recruiter_user), None) is True


def test_a_pending_recruiter_is_told_their_profile_is_in_review(
    pending_recruiter_user: User,
) -> None:
    permission = IsApprovedRecruiter()

    permission.has_permission(_request(pending_recruiter_user), None)

    assert permission.message == WAITING_MESSAGE


def test_a_company_user_is_not_told_about_a_review_they_never_asked_for(
    company_admin: User,
) -> None:
    permission = IsApprovedRecruiter()

    assert permission.has_permission(_request(company_admin), None) is False
    assert permission.message == NOT_A_RECRUITER_MESSAGE
