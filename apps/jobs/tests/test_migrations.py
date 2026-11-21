from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.chat.models import Channel
from apps.companies.models import Company
from apps.jobs.models import BountyType, Job, JobAssignment, JobStatus
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.models import RecruiterStatus
from apps.recruiters.tests.factories import RecruiterProfileFactory

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


BEFORE_ASSIGN = ("jobs", "0011_job_guarantee_days")


def _leaves(executor: MigrationExecutor, **apps: str) -> list[tuple[str, str]]:
    """Every app at its latest migration, except the ones named, which stop where given."""
    return [(app, apps.get(app, name)) for app, name in executor.loader.graph.leaf_nodes()]


@pytest.mark.django_db(transaction=True)
def test_pending_invites_become_assignments() -> None:
    """Spec: a headhunter Sparktal invited and who was still deciding is now on the job.

    The before-state is built by migrating the database back past the conversion, since the
    live app registry no longer has `JobInvite`; it is migrated forward again however the
    test ends.
    """
    executor = MigrationExecutor(connection)
    before = _leaves(executor, jobs=BEFORE_ASSIGN[1])
    executor.migrate(before)
    try:
        executor.loader.build_graph()
        old = executor.loader.project_state(before).apps
        JobInvite = old.get_model("jobs", "JobInvite")
        OldAssignment = old.get_model("jobs", "JobAssignment")

        job = JobFactory(on_marketplace=False)
        waiting = RecruiterProfileFactory(nickname="Bluefin")
        unapproved = RecruiterProfileFactory(nickname="Atlas", status=RecruiterStatus.PENDING)
        declined = RecruiterProfileFactory(nickname="Fog Bank")
        came_back = RecruiterProfileFactory(nickname="NorthStar")
        for recruiter, status in (
            (waiting, "pending"),
            (unapproved, "pending"),
            (declined, "declined"),
            (came_back, "pending"),
        ):
            JobInvite.objects.create(job_id=job.pk, recruiter_id=recruiter.pk, status=status)
        # Through the old model, so no channel comes with it: the migration has to make one.
        removed = OldAssignment.objects.create(
            job_id=job.pk, recruiter_id=came_back.pk, left_at=timezone.now()
        )
        # An invite nobody could answer any more (the old `open_invites`: a live job only) stays
        # nothing: the job was deleted or closed before the conversion.
        deleted_job = JobFactory(on_marketplace=False, deleted_at=timezone.now())
        closed_job = JobFactory(on_marketplace=False, status=JobStatus.CLOSED)
        for dead in (deleted_job, closed_job):
            JobInvite.objects.create(job_id=dead.pk, recruiter_id=waiting.pk, status="pending")
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    held = JobAssignment.objects.filter(job=job, left_at__isnull=True)
    assert sorted(assignment.recruiter.nickname for assignment in held) == ["Bluefin", "NorthStar"]
    assert held.get(recruiter=came_back).pk == removed.pk
    assert not JobAssignment.objects.filter(recruiter__in=[unapproved, declined]).exists()
    channels = Channel.objects.filter(job=job)
    assert sorted(channel.recruiter.nickname for channel in channels) == ["Bluefin", "NorthStar"]
    assert not JobAssignment.objects.filter(job__in=[deleted_job, closed_job]).exists()
    assert not Channel.objects.filter(job__in=[deleted_job, closed_job]).exists()
    assert "jobs_jobinvite" not in connection.introspection.table_names()
