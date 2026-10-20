from collections.abc import Callable
from typing import Any
from unittest import mock

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission
from django.core import mail
from django.db import connection
from django.http import HttpRequest
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import User, UserKind
from apps.recruiters import review
from apps.recruiters.admin import RecruiterProfileAdmin
from apps.recruiters.models import RecruiterProfile, RecruiterStatus
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

Commit = Callable[..., Any]
PASSWORD = "pass-1234-word"


@pytest.fixture
def staff_user(db: None) -> User:
    return User.objects.create_user(
        email="admin@sparktal.vn",
        password=PASSWORD,
        full_name="Sparktal Admin",
        kind=UserKind.STAFF,
        is_staff=True,
    )


class Inbox:
    """The admin messages an action leaves for the user, as (level, text)."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    def add(self, level: int, message: str, extra_tags: str = "") -> None:
        self.sent.append((level, str(message)))

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.sent]


def _admin_request(staff: User, inbox: Inbox | None = None) -> HttpRequest:
    request = RequestFactory().post("/admin/")
    request.user = staff
    request._messages = inbox or Inbox()
    return request


def _model_admin() -> RecruiterProfileAdmin:
    return RecruiterProfileAdmin(RecruiterProfile, AdminSite())


def test_approving_sends_result_email(
    staff_user: User, django_capture_on_commit_callbacks: Commit
) -> None:
    profile = RecruiterProfileFactory(status=RecruiterStatus.PENDING)
    with django_capture_on_commit_callbacks(execute=True):
        _model_admin().approve_profiles(
            _admin_request(staff_user), RecruiterProfile.objects.filter(pk=profile.pk)
        )
    profile.refresh_from_db()
    assert profile.status == RecruiterStatus.APPROVED
    assert profile.reviewed_by == staff_user
    assert len(mail.outbox) == 1
    assert "approved" in mail.outbox[0].subject.lower()
    assert "/r/chat" in mail.outbox[0].body


def test_rejection_requires_a_note(
    staff_user: User, django_capture_on_commit_callbacks: Commit
) -> None:
    profile = RecruiterProfileFactory(status=RecruiterStatus.PENDING, review_note="")
    inbox = Inbox()
    with django_capture_on_commit_callbacks(execute=True):
        _model_admin().reject_profiles(
            _admin_request(staff_user, inbox), RecruiterProfile.objects.filter(pk=profile.pk)
        )
    profile.refresh_from_db()
    assert profile.status == RecruiterStatus.PENDING
    assert mail.outbox == []
    assert inbox.texts == [f"Write a review note on each profile before rejecting it: {profile}"]


def test_rejection_with_one_blank_note_changes_nobody(
    staff_user: User, django_capture_on_commit_callbacks: Commit
) -> None:
    with_note = RecruiterProfileFactory(status=RecruiterStatus.PENDING, review_note="Too thin.")
    blank = RecruiterProfileFactory(status=RecruiterStatus.PENDING, review_note="   ")
    with django_capture_on_commit_callbacks(execute=True):
        _model_admin().reject_profiles(
            _admin_request(staff_user),
            RecruiterProfile.objects.filter(pk__in=[with_note.pk, blank.pk]),
        )
    with_note.refresh_from_db()
    blank.refresh_from_db()
    assert (with_note.status, blank.status) == (RecruiterStatus.PENDING, RecruiterStatus.PENDING)
    assert mail.outbox == []


def test_rejection_emails_the_reason(
    staff_user: User, django_capture_on_commit_callbacks: Commit
) -> None:
    profile = RecruiterProfileFactory(
        status=RecruiterStatus.PENDING, review_note="No track record."
    )
    with django_capture_on_commit_callbacks(execute=True):
        _model_admin().reject_profiles(
            _admin_request(staff_user), RecruiterProfile.objects.filter(pk=profile.pk)
        )
    profile.refresh_from_db()
    assert profile.status == RecruiterStatus.REJECTED
    assert profile.reviewed_by == staff_user
    assert "No track record." in mail.outbox[0].body


def test_approved_profile_is_skipped(
    staff_user: User, django_capture_on_commit_callbacks: Commit
) -> None:
    profile = RecruiterProfileFactory(status=RecruiterStatus.APPROVED)
    inbox = Inbox()
    with django_capture_on_commit_callbacks(execute=True):
        _model_admin().approve_profiles(
            _admin_request(staff_user, inbox), RecruiterProfile.objects.filter(pk=profile.pk)
        )
    assert mail.outbox == []
    assert inbox.texts == ["Approved 0 recruiter(s)."]


def test_rejected_profile_is_skipped(
    staff_user: User, django_capture_on_commit_callbacks: Commit
) -> None:
    """P3-R13: a second reject is a no-op in the admin too, not a `ReviewError` escaping."""
    reviewed_at = timezone.now()
    profile = RecruiterProfileFactory(
        status=RecruiterStatus.REJECTED, review_note="Not a fit.", reviewed_at=reviewed_at
    )
    inbox = Inbox()
    with django_capture_on_commit_callbacks(execute=True):
        _model_admin().reject_profiles(
            _admin_request(staff_user, inbox), RecruiterProfile.objects.filter(pk=profile.pk)
        )
    profile.refresh_from_db()
    assert profile.reviewed_at == reviewed_at
    assert profile.reviewed_by is None
    assert mail.outbox == []
    assert inbox.texts == ["Rejected 0 recruiter(s)."]


def test_django_admin_actions_use_review_service(
    staff_user: User, django_capture_on_commit_callbacks: Commit
) -> None:
    """Both actions go through `review.py`, and report the profiles they really changed."""
    waiting = RecruiterProfileFactory.create_batch(2, status=RecruiterStatus.PENDING)
    approved = RecruiterProfileFactory(status=RecruiterStatus.APPROVED)
    to_reject = RecruiterProfileFactory.create_batch(
        2, status=RecruiterStatus.PENDING, review_note="Not a fit."
    )
    rejected_already = RecruiterProfileFactory(
        status=RecruiterStatus.REJECTED, review_note="Earlier no."
    )
    inbox = Inbox()
    model_admin = _model_admin()

    with (
        mock.patch(
            "apps.recruiters.admin.approve_recruiter", wraps=review.approve_recruiter
        ) as approve_service,
        mock.patch(
            "apps.recruiters.admin.reject_recruiter", wraps=review.reject_recruiter
        ) as reject_service,
        django_capture_on_commit_callbacks(execute=True),
    ):
        model_admin.approve_profiles(
            _admin_request(staff_user, inbox),
            RecruiterProfile.objects.filter(pk__in=[p.pk for p in [*waiting, approved]]),
        )
        model_admin.reject_profiles(
            _admin_request(staff_user, inbox),
            RecruiterProfile.objects.filter(pk__in=[p.pk for p in [*to_reject, rejected_already]]),
        )

    assert approve_service.call_count == 3
    assert reject_service.call_count == 3
    assert inbox.texts == ["Approved 2 recruiter(s).", "Rejected 2 recruiter(s)."]
    statuses = {
        p.pk: p.status
        for p in RecruiterProfile.objects.filter(
            pk__in=[p.pk for p in [*waiting, approved, *to_reject, rejected_already]]
        )
    }
    assert [statuses[p.pk] for p in waiting] == [RecruiterStatus.APPROVED] * 2
    assert [statuses[p.pk] for p in to_reject] == [RecruiterStatus.REJECTED] * 2
    assert statuses[approved.pk] == RecruiterStatus.APPROVED
    assert statuses[rejected_already.pk] == RecruiterStatus.REJECTED
    # One mail for each profile that changed, none for those that did not.
    assert sorted(m.to[0] for m in mail.outbox) == sorted(
        p.user.email for p in [*waiting, *to_reject]
    )


def test_approval_mail_waits_for_the_commit(staff_user: User) -> None:
    profile = RecruiterProfileFactory(status=RecruiterStatus.PENDING)
    _model_admin().approve_profiles(
        _admin_request(staff_user), RecruiterProfile.objects.filter(pk=profile.pk)
    )
    assert mail.outbox == []


def test_a_review_locks_only_the_profile_row(staff_user: User) -> None:
    profile = RecruiterProfileFactory(status=RecruiterStatus.PENDING)

    with CaptureQueriesContext(connection) as queries:
        review.approve_recruiter(profile, by=staff_user)

    locks = [q["sql"] for q in queries.captured_queries if "FOR UPDATE" in q["sql"]]
    assert locks, "the review must lock the profile"
    profile_table = RecruiterProfile._meta.db_table
    assert all(sql.endswith(f'FOR UPDATE OF "{profile_table}"') for sql in locks)


# Permissions ------------------------------------------------------------------------------------


def _view_only_staff(codename: str) -> User:
    user = User.objects.create_user(
        email="viewer@sparktal.vn",
        password=PASSWORD,
        full_name="Viewer",
        kind=UserKind.STAFF,
        is_staff=True,
    )
    user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


def test_actions_need_the_change_permission(staff_user: User, rf: RequestFactory) -> None:
    viewer = _view_only_staff("view_recruiterprofile")
    request = rf.get("/admin/recruiters/recruiterprofile/")

    request.user = viewer
    assert {"approve_profiles", "reject_profiles"}.isdisjoint(_model_admin().get_actions(request))

    request.user = User.objects.create_superuser("root@sparktal.vn", PASSWORD, full_name="Root")
    assert {"approve_profiles", "reject_profiles"} <= set(_model_admin().get_actions(request))


@pytest.mark.parametrize("action", ["approve_profiles", "reject_profiles"])
def test_view_only_staff_cannot_run_the_review_actions(
    action: str, django_capture_on_commit_callbacks: Commit
) -> None:
    _view_only_staff("view_recruiterprofile")
    profile = RecruiterProfileFactory(status=RecruiterStatus.PENDING, review_note="Not a fit.")
    client = Client()
    assert client.login(email="viewer@sparktal.vn", password=PASSWORD)
    url = "/admin/recruiters/recruiterprofile/"

    page = client.get(url)
    assert page.status_code == 200
    assert 'value="approve_profiles"' not in page.content.decode()
    assert 'value="reject_profiles"' not in page.content.decode()

    with django_capture_on_commit_callbacks(execute=True):
        client.post(url, {"action": action, "_selected_action": [str(profile.pk)]})

    profile.refresh_from_db()
    assert profile.status == RecruiterStatus.PENDING
    assert mail.outbox == []
