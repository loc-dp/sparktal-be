from argparse import ArgumentParser
from importlib import import_module
from importlib.util import find_spec
from typing import Any

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.seed import DEMO_EMAILS, SeedContext, run_seeders


class Command(BaseCommand):
    help = "Create (or refresh) the demo data used for local development and E2E tests."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the demo users (and everything that cascades from them) first.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Required to seed when DEBUG is off (the demo accounts share a public password).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "seed_demo creates accounts with a publicly known password and refuses to run "
                "with DEBUG off. Pass --force if this really is a throwaway environment."
            )

        for app_config in apps.get_app_configs():
            module_name = f"{app_config.name}.seed"
            if app_config.name.startswith("apps.") and find_spec(module_name) is not None:
                import_module(module_name)

        with transaction.atomic():
            if options["reset"]:
                deleted, _ = get_user_model().objects.filter(email__in=DEMO_EMAILS).delete()
                self.stdout.write(f"Reset: deleted {deleted} rows.")
            ran = run_seeders(SeedContext())

        for name in ran:
            self.stdout.write(f"  ran {name}")
        self.stdout.write(self.style.SUCCESS(f"Demo data ready ({len(ran)} seeders)."))
