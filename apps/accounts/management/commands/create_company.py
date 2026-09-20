from argparse import ArgumentParser
from typing import Any

from allauth.account.forms import ResetPasswordForm
from allauth.core import context
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import RequestFactory

from apps.accounts.models import User, UserKind


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

        # Plan 03 creates the Company row and the admin CompanyMembership here.
        self.stdout.write(f"Created company admin {user.email} for “{options['name']}”.")
        self.stdout.write(
            self.style.WARNING("Company profile is created on first sign-in (career page setup).")
        )

        if not options["no_email"]:
            # allauth reads the current request from a context var, so a bare
            # RequestFactory request is not enough: it has to be pushed in. Its
            # default host ("testserver") is rejected by ALLOWED_HOSTS outside
            # tests, so borrow the first allowed host.
            host = next(iter(settings.ALLOWED_HOSTS), "localhost")
            request = RequestFactory().post("/", HTTP_HOST=host)
            with context.request_context(request):
                form = ResetPasswordForm(data={"email": email})
                if not form.is_valid():
                    raise CommandError(f"Could not send the invite: {form.errors.as_text()}")
                form.save(request)
            self.stdout.write(self.style.SUCCESS(f"Invite sent to {email}."))
