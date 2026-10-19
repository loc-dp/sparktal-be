from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.db import connection

from apps.companies.models import Company
from apps.jobs.models import BountyType, Job, JobStatus
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db


def _backfill_distribution() -> None:
    """Run the data migration against the live models, as the companies migration tests do."""
    migration = import_module("apps.jobs.migrations.0009_backfill_distribution")
    migration.backfill(django_apps, SimpleNamespace(connection=connection))


def _bounty(job: Job) -> tuple[str | None, int | None, int | None]:
    job.refresh_from_db()
    amount = job.bounty_amount
    return job.bounty_type, job.bounty_pct, None if amount is None else int(amount)


@pytest.mark.parametrize("status", [JobStatus.DRAFT, JobStatus.SUBMITTED, JobStatus.PENDING])
@pytest.mark.parametrize(
    "bounty",
    [
        {"bounty_type": BountyType.PCT, "bounty_pct": 15},  # the old default
        {"bounty_type": BountyType.FIXED, "bounty_pct": None, "bounty_amount": 90_000_000},
    ],
)
def test_jobs_not_yet_distributed_lose_the_bounty_their_company_chose(
    company: Company, status: str, bounty: dict[str, object]
) -> None:
    # Spec decision 11: Sparktal enters the bounty before it distributes a job.
    job = JobFactory(company=company, status=status, on_marketplace=False, **bounty)

    _backfill_distribution()

    assert _bounty(job) == (None, None, None)


@pytest.mark.parametrize("status", [JobStatus.ACTIVE, JobStatus.CLOSED])
def test_live_and_closed_jobs_keep_their_bounty(company: Company, status: str) -> None:
    pct = JobFactory(
        company=company, status=status, bounty_type=BountyType.PCT, bounty_pct=18, title="Pct"
    )
    fixed = JobFactory(
        company=company,
        status=status,
        bounty_type=BountyType.FIXED,
        bounty_pct=None,
        bounty_amount=90_000_000,
        title="Fixed",
    )

    _backfill_distribution()

    assert _bounty(pct) == ("pct", 18, None)
    assert _bounty(fixed) == ("fixed", None, 90_000_000)


def test_the_history_keeps_the_bounty_it_recorded(company: Company) -> None:
    job = JobFactory(company=company, status=JobStatus.PENDING, bounty_pct=15, on_marketplace=False)
    recorded = [(row.history_id, row.bounty_pct) for row in job.history.all()]

    _backfill_distribution()

    assert _bounty(job) == (None, None, None)
    assert (
        [(row.history_id, row.bounty_pct) for row in job.history.all()]
        == recorded
        == [(recorded[0][0], 15)]
    )
