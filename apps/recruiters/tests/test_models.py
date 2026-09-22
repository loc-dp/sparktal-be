import pytest
from django.db import IntegrityError

from apps.accounts.models import User
from apps.recruiters.models import RecruiterStatus
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db


def test_nickname_is_unique() -> None:
    RecruiterProfileFactory(nickname="NorthStar Talent")

    with pytest.raises(IntegrityError):
        RecruiterProfileFactory(nickname="NorthStar Talent")


def test_str_is_the_nickname() -> None:
    profile = RecruiterProfileFactory(nickname="Bluefin Search")

    assert str(profile) == "Bluefin Search"
    assert profile.user.kind == "recruiter"


def test_new_recruiter_waits_for_review() -> None:
    profile = RecruiterProfileFactory(status=RecruiterStatus.PENDING)

    assert profile.status == RecruiterStatus.PENDING
    assert profile.reviewed_at is None


def test_approval_records_reviewer_and_clears_note(company_admin: User) -> None:
    profile = RecruiterProfileFactory(status=RecruiterStatus.PENDING, review_note="Needs a call")
    profile.approve(by=company_admin)
    profile.refresh_from_db()

    assert profile.status == RecruiterStatus.APPROVED
    assert profile.reviewed_by == company_admin
    assert profile.reviewed_at is not None
    assert profile.review_note == ""


def test_rejection_records_reason(company_admin: User) -> None:
    profile = RecruiterProfileFactory(status=RecruiterStatus.PENDING)
    profile.reject(by=company_admin, note="No verifiable track record.")
    profile.refresh_from_db()

    assert profile.status == RecruiterStatus.REJECTED
    assert profile.review_note == "No verifiable track record."


def test_nickname_is_case_insensitively_unique() -> None:
    RecruiterProfileFactory(nickname="NorthStar Talent")
    with pytest.raises(IntegrityError):
        RecruiterProfileFactory(nickname="northstar talent")
