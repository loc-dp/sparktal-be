from collections.abc import Callable
from datetime import datetime, timedelta
from importlib import import_module
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.accounts.tests.factories import UserFactory
from apps.chat.models import Channel
from apps.companies.models import Company
from apps.jobs.models import BountyType, Job, JobAssignment, JobComment, JobStatus
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.models import RecruiterProfile, RecruiterStatus
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


BEFORE_COMMENTS = ("jobs", "0012_invites_become_assignments")


def _before_comments[T](build: Callable[[Any], T]) -> T:
    """Run `build(JobQuestion)` with the database just before the conversion, then migrate it
    forward again, however `build` ends.

    The live registry no longer has `JobQuestion`, so `build` gets the historical model.
    """
    executor = MigrationExecutor(connection)
    before = _leaves(executor, jobs=BEFORE_COMMENTS[1])
    executor.migrate(before)
    try:
        executor.loader.build_graph()
        return build(executor.loader.project_state(before).apps.get_model("jobs", "JobQuestion"))
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


def _ask(
    JobQuestion: Any, job: Job, asker: RecruiterProfile, asked: datetime, **fields: Any
) -> Any:
    question = JobQuestion.objects.create(job_id=job.pk, asked_by_id=asker.pk, **fields)
    # `created_at` is `auto_now_add`: set it after the insert.
    JobQuestion.objects.filter(pk=question.pk).update(created_at=asked)
    question.refresh_from_db()
    return question


def _parent_body(reply: JobComment) -> str:
    assert reply.parent is not None
    return reply.parent.body


@pytest.mark.django_db(transaction=True)
def test_questions_become_comments() -> None:
    """Spec §5.3: each question is a headhunter's top-level comment, its answer a reply, both
    at the moments they were written; a hidden question is a hidden comment."""
    job = JobFactory(on_marketplace=False)
    asker = RecruiterProfileFactory(nickname="NorthStar")
    answerer = UserFactory(email="maianh@velapay.vn")
    now = timezone.now()
    hidden = now - timedelta(hours=1)

    def build(JobQuestion: Any) -> dict[str, Any]:
        return {
            "q1": _ask(
                JobQuestion,
                job,
                asker,
                now - timedelta(days=3),
                body="Is the range negotiable?",
                answer="Up to 62M.",
                answered_by_id=answerer.pk,
                answered_at=now - timedelta(days=2),
            ),
            "q2": _ask(
                JobQuestion, job, asker, now - timedelta(days=1), body="Remote from Da Nang?"
            ),
            "q3": _ask(
                JobQuestion,
                job,
                asker,
                now - timedelta(hours=8),
                body="Notice period?",
                answer="45 days.",
                answered_by_id=answerer.pk,
                answered_at=now - timedelta(hours=5),
                hidden_at=hidden,
            ),
        }

    made = _before_comments(build)

    top = JobComment.objects.filter(job=job, parent__isnull=True)
    assert top.count() == 3
    assert all(comment.from_recruiter for comment in top)
    assert all(comment.author_id == asker.user_id for comment in top)
    by_body = {comment.body: comment for comment in top}
    for question in made.values():
        assert by_body[question.body].created_at == question.created_at
    assert by_body["Notice period?"].hidden_at == hidden
    assert by_body["Is the range negotiable?"].hidden_at is None
    assert by_body["Remote from Da Nang?"].hidden_at is None

    replies = JobComment.objects.filter(job=job, parent__isnull=False)
    assert replies.count() == 2
    for reply in replies:
        assert reply.from_recruiter is False
        assert reply.author_id == answerer.pk
        assert reply.hidden_at is None
    assert {(_parent_body(reply), reply.body, reply.created_at) for reply in replies} == {
        ("Is the range negotiable?", "Up to 62M.", made["q1"].answered_at),
        ("Notice period?", "45 days.", made["q3"].answered_at),
    }
    assert "jobs_jobquestion" not in connection.introspection.table_names()


@pytest.mark.django_db(transaction=True)
def test_answer_without_a_time_or_an_answerer_still_becomes_a_reply() -> None:
    """An answer with no `answered_at` is dated like its question, so it never reads as new;
    one whose answerer's account is gone keeps no author but is still the company's."""
    job = JobFactory(on_marketplace=False)
    asker = RecruiterProfileFactory()
    asked = timezone.now() - timedelta(days=5)

    def build(JobQuestion: Any) -> Any:
        _ask(JobQuestion, job, asker, asked, body="Blank answer?", answer="   ")
        return _ask(JobQuestion, job, asker, asked, body="Undated?", answer="Yes.")

    undated = _before_comments(build)

    reply = JobComment.objects.get(job=job, parent__isnull=False)
    assert (_parent_body(reply), reply.body) == (undated.body, "Yes.")
    assert reply.created_at == undated.created_at
    assert (reply.author, reply.from_recruiter) == (None, False)
    assert JobComment.objects.filter(job=job, parent__isnull=True).count() == 2
