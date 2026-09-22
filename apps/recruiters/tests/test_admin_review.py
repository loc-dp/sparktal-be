import pytest
from django.contrib.admin.sites import AdminSite
from django.core import mail
from django.http import HttpRequest
from django.test import RequestFactory

from apps.accounts.models import User, UserKind
from apps.recruiters.admin import RecruiterProfileAdmin
from apps.recruiters.models import RecruiterProfile, RecruiterStatus
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_user(db: None) -> User:
    return User.objects.create_user(
        email="admin@sparktal.vn",
        password="pass-1234-word",
        full_name="Sparktal Admin",
        kind=UserKind.STAFF,
        is_staff=True,
    )


def _admin_request(staff: User) -> HttpRequest:
    request = RequestFactory().post("/admin/")
    request.user = staff
    request._messages = type("Store", (), {"add": lambda *args, **kwargs: None})()
    return request


def test_approving_sends_result_email(staff_user: User) -> None:
    profile = RecruiterProfileFactory(status=RecruiterStatus.PENDING)
    admin = RecruiterProfileAdmin(RecruiterProfile, AdminSite())
    admin.approve_profiles(
        _admin_request(staff_user), RecruiterProfile.objects.filter(pk=profile.pk)
    )
    profile.refresh_from_db()
    assert profile.status == RecruiterStatus.APPROVED
    assert profile.reviewed_by == staff_user
    assert len(mail.outbox) == 1
    assert "approved" in mail.outbox[0].subject.lower()
    assert "/r/chat" in mail.outbox[0].body


def test_rejection_requires_a_note(staff_user: User) -> None:
    profile = RecruiterProfileFactory(status=RecruiterStatus.PENDING, review_note="")
    admin = RecruiterProfileAdmin(RecruiterProfile, AdminSite())
    admin.reject_profiles(
        _admin_request(staff_user), RecruiterProfile.objects.filter(pk=profile.pk)
    )
    profile.refresh_from_db()
    assert profile.status == RecruiterStatus.PENDING
    assert mail.outbox == []


def test_rejection_emails_the_reason(staff_user: User) -> None:
    profile = RecruiterProfileFactory(
        status=RecruiterStatus.PENDING, review_note="No track record."
    )
    admin = RecruiterProfileAdmin(RecruiterProfile, AdminSite())
    admin.reject_profiles(
        _admin_request(staff_user), RecruiterProfile.objects.filter(pk=profile.pk)
    )
    profile.refresh_from_db()
    assert profile.status == RecruiterStatus.REJECTED
    assert "No track record." in mail.outbox[0].body


def test_approved_profile_is_skipped(staff_user: User) -> None:
    profile = RecruiterProfileFactory(status=RecruiterStatus.APPROVED)
    admin = RecruiterProfileAdmin(RecruiterProfile, AdminSite())
    admin.approve_profiles(
        _admin_request(staff_user), RecruiterProfile.objects.filter(pk=profile.pk)
    )
    assert mail.outbox == []
