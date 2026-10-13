from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.invites import InviteError, send_password_invite
from apps.accounts.models import User, UserKind
from apps.companies.models import Company, CompanyMembership, CompanyRole


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
        email = User.objects.normalize_email(options["admin_email"])
        if User.objects.filter(email=email).exists():
            raise CommandError(f"A user with email {email} already exists.")

        user = User.objects.create_user(
            email=email, password=None, full_name=options["admin_name"], kind=UserKind.COMPANY
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])

        company = Company.objects.create(name=options["name"])
        CompanyMembership.objects.create(user=user, company=company, role=CompanyRole.ADMIN)
        self.stdout.write(
            f"Created company “{company.name}” with admin {user.email}. "
            "The profile is completed on first sign-in (career page setup)."
        )

        if not options["no_email"]:
            try:
                send_password_invite(
                    email,
                    company_name=company.name,
                    role_label=CompanyRole.ADMIN.label,
                    invited_by="The Sparktal team",
                )
            except InviteError as exc:
                raise CommandError(f"Could not send the invite: {exc}") from exc
            self.stdout.write(self.style.SUCCESS(f"Invite sent to {email}."))
