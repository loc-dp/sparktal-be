from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from allauth.account.forms import ResetPasswordForm
from allauth.core import context
from django.conf import settings
from django.test import RequestFactory


class InviteError(Exception):
    """allauth refused to send the set-password email."""


@dataclass(frozen=True)
class InviteDetails:
    """Who invited the person, and to what. Read by the account adapter to pick the invite email."""

    company_name: str | None = None
    role_label: str | None = None
    invited_by: str | None = None

    @property
    def facts(self) -> list[tuple[str, str]]:
        rows = [
            ("Company", self.company_name),
            ("Your role", self.role_label),
            ("Invited by", self.invited_by),
        ]
        return [(label, value) for label, value in rows if value]


_current_invite: ContextVar[InviteDetails | None] = ContextVar("current_invite", default=None)


def current_invite() -> InviteDetails | None:
    return _current_invite.get()


@contextmanager
def _inviting(details: InviteDetails) -> Iterator[None]:
    token = _current_invite.set(details)
    try:
        yield
    finally:
        _current_invite.reset(token)


def send_password_invite(
    email: str,
    *,
    company_name: str | None = None,
    role_label: str | None = None,
    invited_by: str | None = None,
) -> None:
    """Email `email` an invite with a set-password link (the platform has no self sign-up)."""
    host = next(iter(settings.ALLOWED_HOSTS), "localhost")
    request = RequestFactory().post("/", HTTP_HOST=host)
    details = InviteDetails(company_name, role_label, invited_by)
    with context.request_context(request), _inviting(details):
        form = ResetPasswordForm(data={"email": email})
        if not form.is_valid():
            raise InviteError(form.errors.as_text())
        form.save(request)
