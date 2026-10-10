from allauth.account.forms import ResetPasswordForm
from allauth.core import context
from django.conf import settings
from django.test import RequestFactory


class InviteError(Exception):
    """allauth refused to send the set-password email."""


def send_password_invite(email: str) -> None:
    """Email `email` a set-password link (the platform has no self sign-up)."""
    host = next(iter(settings.ALLOWED_HOSTS), "localhost")
    request = RequestFactory().post("/", HTTP_HOST=host)
    with context.request_context(request):
        form = ResetPasswordForm(data={"email": email})
        if not form.is_valid():
            raise InviteError(form.errors.as_text())
        form.save(request)
