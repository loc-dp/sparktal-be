import pytest
from django.core import mail

from apps.accounts.invites import send_password_invite
from apps.accounts.models import User, UserKind
from apps.core.emails import build_email

pytestmark = pytest.mark.django_db


def html_of(message: mail.EmailMessage) -> str:
    return str(message.alternatives[0][0])


def test_invite_email_reads_as_an_invite_not_a_reset() -> None:
    user = User.objects.create_user(
        email="new.hire@velapay.vn", password=None, full_name="Linh Tran", kind=UserKind.COMPANY
    )
    user.set_unusable_password()
    user.save()

    send_password_invite(
        "new.hire@velapay.vn",
        company_name="VelaPay",
        role_label="Hiring Manager",
        invited_by="Minh Nguyen",
    )

    message = mail.outbox[0]
    assert message.subject == "Join VelaPay on Sparktal"
    html = html_of(message)
    for text in (message.body, html):
        assert "Minh Nguyen" in text
        assert "VelaPay" in text
        assert "reset-password?key=" in text
        assert "password reset" not in text.lower()
    assert "Hiring Manager" in html
    assert "[" not in message.subject  # no "[api.host] " prefix


def test_invite_without_details_still_renders() -> None:
    user = User.objects.create_user(email="a@velapay.vn", password=None, full_name="")
    user.set_unusable_password()
    user.save()

    send_password_invite("a@velapay.vn")

    assert mail.outbox[0].subject == "Set up your Sparktal account"
    assert "Welcome to Sparktal." in html_of(mail.outbox[0])


def test_forgot_password_keeps_the_reset_wording() -> None:
    User.objects.create_user(email="a@velapay.vn", password="pass-1234-word")
    # The reset form without an invite in flight is what the forgot-password page uses.
    from allauth.account.forms import ResetPasswordForm
    from allauth.core import context
    from django.test import RequestFactory

    request = RequestFactory().post("/", HTTP_HOST="localhost")
    with context.request_context(request):
        form = ResetPasswordForm(data={"email": "a@velapay.vn"})
        assert form.is_valid()
        form.save(request)

    assert mail.outbox[0].subject == "Reset your Sparktal password"
    assert "Choose a new password." in html_of(mail.outbox[0])


def test_build_email_attaches_html_and_text_bodies() -> None:
    message = build_email(
        "email/recruiter_rejected",
        "r@example.com",
        {"nickname": "Rae", "review_note": "No <b>track</b> record."},
    )

    assert message.to == ["r@example.com"]
    assert "No <b>track</b> record." in message.body
    assert "No &lt;b&gt;track&lt;/b&gt; record." in html_of(message)
