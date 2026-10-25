"""Recruiters ask a job's company questions; the company answers them (the Q&A card).

A question is open until it is answered. Only an answered question is shown to the other
recruiters on the job; an unanswered one stays between its asker and the company.
"""

from uuid import UUID

from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, QuerySet, Value, When
from django.utils import timezone

from apps.accounts.models import User
from apps.jobs.access import jobs_joined_by, jobs_offered_to
from apps.jobs.models import Job, JobQuestion, JobStatus
from apps.jobs.notifications import notify_question_answered, notify_question_asked
from apps.recruiters.models import RecruiterProfile

QUESTION_MAX_LENGTH = 1000
ANSWER_MAX_LENGTH = 2000
MAX_OPEN_QUESTIONS = 5

JOB_NOT_TAKING_QUESTIONS = "This job no longer takes questions."
TOO_MANY_OPEN = "You already have 5 questions waiting for an answer on this job."


class QuestionRejected(Exception):
    """The question cannot be asked; the message says why, in the user's words."""


def recruiter_question_jobs(recruiter: RecruiterProfile | None) -> QuerySet[Job]:
    """The jobs whose Q&A `recruiter` may open: those offered to them and those they are on."""
    return Job.objects.filter(
        Q(pk__in=jobs_offered_to(recruiter).values("pk"))
        | Q(pk__in=jobs_joined_by(recruiter).values("pk"))
    )


def _open(questions: QuerySet[JobQuestion]) -> QuerySet[JobQuestion]:
    return questions.filter(answered_at__isnull=True, hidden_at__isnull=True)


def visible_to_recruiter(job: Job, recruiter: RecruiterProfile) -> QuerySet[JobQuestion]:
    """Every answered question on `job`, plus `recruiter`'s own; a hidden one is never shown."""
    return JobQuestion.objects.filter(job=job, hidden_at__isnull=True).filter(
        Q(answered_at__isnull=False) | Q(asked_by=recruiter)
    )


def visible_to_company(job: Job) -> QuerySet[JobQuestion]:
    """What waits for an answer first (oldest first), then the answered, newest answer first."""
    return (
        JobQuestion.objects.filter(job=job, hidden_at__isnull=True)
        .annotate(
            answered_rank=Case(
                When(answered_at__isnull=True, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        # Unanswered rows all tie on `answered_at`, so `created_at` orders them.
        .order_by("answered_rank", "-answered_at", "created_at")
    )


def ask_question(job: Job, recruiter: RecruiterProfile, body: str) -> JobQuestion:
    if job.status != JobStatus.ACTIVE or job.deleted_at is not None:
        raise QuestionRejected(JOB_NOT_TAKING_QUESTIONS)
    with transaction.atomic():
        # Lock the asker so two requests at once cannot both slip under the limit.
        RecruiterProfile.objects.select_for_update().get(pk=recruiter.pk)
        if _open(job.questions.filter(asked_by=recruiter)).count() >= MAX_OPEN_QUESTIONS:
            raise QuestionRejected(TOO_MANY_OPEN)
        question = JobQuestion.objects.create(job=job, asked_by=recruiter, body=body)
    notify_question_asked(question)
    return question


def answer_question(question: JobQuestion, user: User, answer: str) -> JobQuestion:
    """Save `answer`; the asker is told once, the first time, and a later edit stays quiet."""
    first_time = question.answered_at is None
    question.answer = answer
    question.answered_by = user
    update_fields = ["answer", "answered_by"]
    if first_time:
        question.answered_at = timezone.now()
        update_fields.append("answered_at")
    question.save(update_fields=update_fields)
    if first_time:
        notify_question_answered(question)
    return question


def hide_question(question: JobQuestion) -> None:
    question.hidden_at = timezone.now()
    question.save(update_fields=["hidden_at"])


def unanswered_counts(jobs: QuerySet[Job]) -> dict[UUID, int]:
    """Open questions per job, for the jobs that have any."""
    rows = (
        _open(JobQuestion.objects.filter(job__in=jobs))
        .order_by()
        .values("job_id")
        .annotate(total=Count("pk"))
    )
    return {row["job_id"]: row["total"] for row in rows}
