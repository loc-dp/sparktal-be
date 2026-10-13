from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.http import HttpRequest

from apps.accounts.invites import current_invite
from apps.core.emails import email_branding

# allauth's own email templates, swapped for the Sparktal-branded ones in apps/core.
RESET_KEY = "account/email/password_reset_key"
BRANDED_TEMPLATES = {
    RESET_KEY: "email/password_reset",
    "account/email/unknown_account": "email/unknown_account",
}
INVITE_TEMPLATE = "email/invite"


class AccountAdapter(DefaultAccountAdapter):
    """Accounts are provisioned by the platform, never self-service."""

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return False

    def format_email_subject(self, subject: str) -> str:
        # allauth would prefix "[<host>] ", which here is the API host name.
        return subject

    def send_mail(self, template_prefix: str, email: str, context: dict[str, Any]) -> None:
        ctx = {
            **email_branding(),
            "expiry_days": settings.ACCOUNT_PASSWORD_RESET_TOKEN_EXPIRY // 86_400,
            **context,
        }
        prefix = BRANDED_TEMPLATES.get(template_prefix, template_prefix)
        # A set-password mail sent while inviting someone reads as an invite, not a reset.
        if template_prefix == RESET_KEY and (invite := current_invite()):
            prefix = INVITE_TEMPLATE
            ctx.update(
                company_name=invite.company_name,
                invited_by=invite.invited_by,
                facts=invite.facts,
            )
        super().send_mail(prefix, email, ctx)
