import pytest
from django.core import mail

from apps.accounts.invites import InviteError, send_password_invite
from apps.accounts.models import User

pytestmark = pytest.mark.django_db


def test_sends_a_set_password_email_to_an_existing_user() -> None:
    user = User.objects.create_user(
        email="new.hire@velapay.vn", password=None, full_name="New Hire"
    )
    user.set_unusable_password()
    user.save()

    send_password_invite("new.hire@velapay.vn")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["new.hire@velapay.vn"]


def test_unknown_email_gets_allauths_unknown_account_notice_without_a_reset_link() -> None:
    # allauth's EMAIL_UNKNOWN_ACCOUNTS defaults to True: an unknown address receives an
    # "Unknown Account" notice (no reset link) rather than silence. The pre-extraction
    # create_company behaved the same, so the helper must too.
    send_password_invite("nobody@example.com")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject.endswith("Unknown Account")
    assert "reset-password" not in mail.outbox[0].body


def test_invite_error_is_an_exception() -> None:
    assert issubclass(InviteError, Exception)
