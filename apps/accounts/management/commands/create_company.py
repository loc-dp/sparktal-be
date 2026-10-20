from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.accounts.invites import InviteError, send_password_invite
from apps.companies.models import CompanyRole
from apps.companies.provisioning import SPARKTAL_INVITER, provision_company


def _sentences(error: ValidationError) -> str:
    detail = error.detail
    if isinstance(detail, dict):
        return " ".join(str(message) for messages in detail.values() for message in messages)
    return str(detail)


class Command(BaseCommand):
    help = "Provision a company account and invite its first admin by email."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--name", required=True, help="Company name")
        parser.add_argument("--admin-email", required=True)
        parser.add_argument("--admin-name", required=True)
        parser.add_argument(
            "--no-email", action="store_true", help="Create the account without sending the invite"
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        # The ops portal's "Create company" does the same: `POST /api/v1/ops/companies`.
        try:
            admin = provision_company(
                name=options["name"],
                admin_email=options["admin_email"],
                admin_full_name=options["admin_name"],
            )
        except ValidationError as exc:
            raise CommandError(_sentences(exc)) from exc
        company, email = admin.company, admin.user.email
        self.stdout.write(
            f"Created company “{company.name}” with admin {email}. "
            "The profile is completed on first sign-in (career page setup)."
        )

        if not options["no_email"]:
            try:
                send_password_invite(
                    email,
                    company_name=company.name,
                    role_label=CompanyRole.ADMIN.label,
                    invited_by=SPARKTAL_INVITER,
                )
            except InviteError as exc:  # rolls the whole account back
                raise CommandError(f"Could not send the invite: {exc}") from exc
            self.stdout.write(self.style.SUCCESS(f"Invite sent to {email}."))
