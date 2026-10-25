from datetime import UTC, date, datetime
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.db import connection

from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory
from apps.hiring.models import Placement
from apps.hiring.tests.factories import PlacementFactory
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db


def _backfill() -> None:
    """Run the data migration against the live models, as the jobs migration tests do."""
    migration = import_module("apps.hiring.migrations.0003_backfill_placements")
    migration.backfill(django_apps, SimpleNamespace(connection=connection))


def _hired_at(application: Application, moment: datetime) -> None:
    Application.objects.filter(pk=application.pk).update(stage_changed_at=moment)


def test_backfill_creates_placements() -> None:
    job = JobFactory(guarantee_days=45)
    hired = ApplicationFactory(job=job, stage=Stage.ONBOARDED)
    # 03:00 UTC is 10:00 in Ho Chi Minh City: the same calendar day.
    _hired_at(hired, datetime(2026, 8, 3, 3, 0, tzinfo=UTC))
    still_interviewing = ApplicationFactory(job=job, stage=Stage.INTERVIEW)

    _backfill()

    placement = Placement.objects.get(application=hired)
    assert (placement.start_date, placement.guarantee_days) == (date(2026, 8, 3), 45)
    assert not Placement.objects.filter(application=still_interviewing).exists()


def test_backfill_uses_the_local_day_of_the_hire() -> None:
    hired = ApplicationFactory(stage=Stage.ONBOARDED)
    # 20:00 UTC is already the next morning in Ho Chi Minh City.
    _hired_at(hired, datetime(2026, 8, 3, 20, 0, tzinfo=UTC))

    _backfill()

    assert Placement.objects.get(application=hired).start_date == date(2026, 8, 4)


def test_backfill_leaves_existing_placements_alone_and_can_run_twice() -> None:
    kept = PlacementFactory(start_date=date(2026, 9, 1), guarantee_days=30)
    ApplicationFactory(stage=Stage.ONBOARDED)

    _backfill()
    _backfill()

    assert Placement.objects.count() == 2
    kept.refresh_from_db()
    assert (kept.start_date, kept.guarantee_days) == (date(2026, 9, 1), 30)
