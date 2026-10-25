"""Recruiters ask a job's company questions; the company answers (the Q&A card)."""

from datetime import timedelta
from typing import Any, cast

import pytest
from django.conf import settings
from django.utils import timezone

from apps.companies.models import CompanyMembership, CompanyRole
from apps.companies.tests.factories import CompanyMembershipFactory
from apps.jobs.models import Job, JobQuestion, JobStatus
from apps.jobs.questions import (
    JOB_NOT_TAKING_QUESTIONS,
    MAX_OPEN_QUESTIONS,
    TOO_MANY_OPEN,
    QuestionRejected,
    answer_question,
    ask_question,
    hide_question,
    recruiter_question_jobs,
    unanswered_counts,
    visible_to_company,
    visible_to_recruiter,
)
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory, JobQuestionFactory
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db


def _recruiter() -> RecruiterProfile:
    return cast(RecruiterProfile, RecruiterProfileFactory())


def _recipients(mailbox: list[Any]) -> list[str]:
    return sorted({address for message in mailbox for address in message.to})


def test_ask_on_active_job_creates_and_emails(team_world: TeamWorld, mailoutbox: list[Any]) -> None:
    # An admin who was deactivated hears nothing; a second admin does.
    other_admin = cast(
        CompanyMembership,
        CompanyMembershipFactory(
            company=team_world.company, role=CompanyRole.ADMIN, user__email="second@orbit.vn"
        ),
    )
    gone = cast(
        CompanyMembership,
        CompanyMembershipFactory(
            company=team_world.company,
            role=CompanyRole.ADMIN,
            user__email="gone@orbit.vn",
            user__is_active=False,
        ),
    )
    recruiter = _recruiter()

    question = ask_question(team_world.job_a, recruiter, "Is remote work possible?")

    assert question.job == team_world.job_a
    assert question.asked_by == recruiter
    assert question.body == "Is remote work possible?"
    assert question.answer == ""
    assert question.answered_at is None
    assert _recipients(mailoutbox) == sorted(
        [team_world.ta_publish.email, team_world.admin.email, other_admin.user.email]
    )
    assert gone.user.email not in _recipients(mailoutbox)
    # The company reads the question where it is answered: the job's Q&A card.
    assert all(
        f"{settings.FRONTEND_URL}/jobs/{team_world.job_a.pk}#questions" in message.body
        for message in mailoutbox
    )


def test_ask_skips_a_creator_who_cannot_answer(
    team_world: TeamWorld, mailoutbox: list[Any]
) -> None:
    # A Talent Acquisition member who may only draft cannot edit a live job, so cannot answer.
    job = team_world.job_a
    job.created_by = team_world.ta_draft
    job.save(update_fields=["created_by"])

    ask_question(job, _recruiter(), "Is remote work possible?")

    assert _recipients(mailoutbox) == [team_world.admin.email]


def test_ask_when_creator_is_admin_emails_each_person_once(
    team_world: TeamWorld, mailoutbox: list[Any]
) -> None:
    ask_question(team_world.job_b, _recruiter(), "Who does the role report to?")

    assert _recipients(mailoutbox) == [team_world.admin.email]
    assert sum(len(message.to) for message in mailoutbox) == 1


@pytest.mark.parametrize(
    "status",
    [JobStatus.DRAFT, JobStatus.SUBMITTED, JobStatus.PENDING, JobStatus.CLOSED],
)
def test_ask_on_closed_job_rejected(
    team_world: TeamWorld, mailoutbox: list[Any], status: JobStatus
) -> None:
    job = team_world.job_a
    job.status = status
    job.save(update_fields=["status"])

    with pytest.raises(QuestionRejected) as error:
        ask_question(job, _recruiter(), "Anything open?")

    assert str(error.value) == JOB_NOT_TAKING_QUESTIONS
    assert not JobQuestion.objects.exists()
    assert mailoutbox == []


def test_sixth_open_question_rejected(team_world: TeamWorld) -> None:
    job = team_world.job_a
    recruiter = _recruiter()
    asked = [ask_question(job, recruiter, f"Question {n}") for n in range(MAX_OPEN_QUESTIONS)]

    with pytest.raises(QuestionRejected) as error:
        ask_question(job, recruiter, "One too many")
    assert str(error.value) == TOO_MANY_OPEN

    # The limit is per recruiter and per job.
    ask_question(job, _recruiter(), "Someone else may ask")
    ask_question(team_world.job_b, recruiter, "Another job is separate")

    answer_question(asked[0], team_world.admin, "Yes.")
    ask_question(job, recruiter, "Now there is room")


def test_hidden_question_does_not_count_as_open(team_world: TeamWorld) -> None:
    job = team_world.job_a
    recruiter = _recruiter()
    asked = [ask_question(job, recruiter, f"Question {n}") for n in range(MAX_OPEN_QUESTIONS)]

    hide_question(asked[0])

    ask_question(job, recruiter, "Room after hiding")


def test_answer_sets_answered_once_and_emails_asker(
    team_world: TeamWorld, mailoutbox: list[Any]
) -> None:
    recruiter = _recruiter()
    question = cast(JobQuestion, JobQuestionFactory(job=team_world.job_a, asked_by=recruiter))

    answer_question(question, team_world.admin, "Yes, up to 10%.")
    question.refresh_from_db()
    first = question.answered_at
    assert first is not None
    assert question.answer == "Yes, up to 10%."
    assert question.answered_by == team_world.admin

    answer_question(question, team_world.ta_publish, "Yes, up to 12%.")
    question.refresh_from_db()
    assert question.answered_at == first
    assert question.answer == "Yes, up to 12%."
    assert question.answered_by == team_world.ta_publish
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [recruiter.user.email]
    # The recruiter's own job page works for a closed job too, unlike the marketplace page.
    assert f"{settings.FRONTEND_URL}/r/jobs/{team_world.job_a.pk}" in mailoutbox[0].body
    assert "/r/marketplace/" not in mailoutbox[0].body


def test_recruiter_sees_answered_and_own_only(team_world: TeamWorld) -> None:
    job = team_world.job_a
    mine, other = _recruiter(), _recruiter()
    now = timezone.now()
    own_open = cast(JobQuestion, JobQuestionFactory(job=job, asked_by=mine))
    other_open = cast(JobQuestion, JobQuestionFactory(job=job, asked_by=other))
    other_answered = cast(
        JobQuestion,
        JobQuestionFactory(
            job=job, asked_by=other, answer="Yes.", answered_by=team_world.admin, answered_at=now
        ),
    )
    other_hidden = cast(JobQuestion, JobQuestionFactory(job=job, asked_by=other, hidden_at=now))
    own_hidden = cast(
        JobQuestion,
        JobQuestionFactory(
            job=job,
            asked_by=mine,
            answer="No.",
            answered_by=team_world.admin,
            answered_at=now,
            hidden_at=now,
        ),
    )
    elsewhere = cast(JobQuestion, JobQuestionFactory(job=team_world.job_b, asked_by=mine))

    seen = set(visible_to_recruiter(job, mine))

    assert seen == {own_open, other_answered}
    assert other_open not in seen
    assert other_hidden not in seen
    assert own_hidden not in seen
    assert elsewhere not in seen
    assert set(visible_to_recruiter(job, other)) == {other_open, other_answered}


def test_company_order_unanswered_first(team_world: TeamWorld) -> None:
    job = team_world.job_a
    now = timezone.now()

    def make(
        minutes_ago: int, answered_minutes_ago: int | None = None, **extra: Any
    ) -> JobQuestion:
        question = cast(JobQuestion, JobQuestionFactory(job=job, **extra))
        JobQuestion.objects.filter(pk=question.pk).update(
            created_at=now - timedelta(minutes=minutes_ago),
            answered_at=None
            if answered_minutes_ago is None
            else now - timedelta(minutes=answered_minutes_ago),
        )
        return question

    old_open = make(50)
    new_open = make(10)
    old_answer = make(60, 30, answer="A")
    new_answer = make(55, 5, answer="B")
    make(40, hidden_at=now)

    assert list(visible_to_company(job)) == [old_open, new_open, new_answer, old_answer]


def test_unanswered_counts_ignore_answered_and_hidden(team_world: TeamWorld) -> None:
    now = timezone.now()
    JobQuestionFactory.create_batch(2, job=team_world.job_a)
    JobQuestionFactory(job=team_world.job_a, hidden_at=now)
    JobQuestionFactory(job=team_world.job_a, answer="Done", answered_at=now)

    counts = unanswered_counts(Job.objects.filter(company=team_world.company))

    assert counts == {team_world.job_a.pk: 2}


def test_recruiter_question_jobs_unions_offered_and_joined_once(team_world: TeamWorld) -> None:
    recruiter = _recruiter()
    # On the marketplace and joined: still one row.
    JobAssignmentFactory(job=team_world.job_a, recruiter=recruiter)
    # Joined, then closed: still reachable through the assignment.
    closed = cast(Job, JobFactory(status=JobStatus.CLOSED, on_marketplace=False))
    JobAssignmentFactory(job=closed, recruiter=recruiter)
    hidden = cast(Job, JobFactory(on_marketplace=False))

    jobs = list(recruiter_question_jobs(recruiter))

    assert sorted(job.pk for job in jobs) == sorted(
        [team_world.job_a.pk, team_world.job_b.pk, closed.pk]
    )
    assert hidden not in jobs
    assert list(recruiter_question_jobs(None)) == []
