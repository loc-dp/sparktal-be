from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from django.http import HttpRequest


class AccountAdapter(DefaultAccountAdapter):
    """Accounts are provisioned by the platform, never self-service."""

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return False

    def send_mail(self, template_prefix: str, email: str, context: dict[str, Any]) -> None:
        super().send_mail(template_prefix, email, context)
