"""The Q&A card's endpoints: a recruiter asks and reads, the company answers and hides."""

from collections.abc import Callable
from typing import cast

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.hiring.tests.conftest import move_to_another_team
from apps.jobs.models import Job, JobQuestion, JobStatus
from apps.jobs.questions import JOB_NOT_TAKING_QUESTIONS, TOO_MANY_OPEN
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory, JobQuestionFactory
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

ClientFor = Callable[[User], APIClient]


def _recruiter_url(job: Job) -> str:
    return f"/api/v1/marketplace/jobs/{job.pk}/questions"


def _company_url(job: Job) -> str:
    return f"/api/v1/jobs/{job.pk}/questions"


def _answered(job: Job, asker: RecruiterProfile, answer: str = "Yes, up to 15%.") -> JobQuestion:
    return cast(
        JobQuestion,
        JobQuestionFactory(
            job=job, asked_by=asker, answer=answer, answered_at=timezone.now(), body="Negotiable?"
        ),
    )


def test_recruiter_lists_answered_and_own(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile, joined_job: Job
) -> None:
    other = cast(RecruiterProfile, RecruiterProfileFactory())
    JobAssignmentFactory(job=joined_job, recruiter=other)
    answered = _answered(joined_job, other)
    mine = cast(JobQuestion, JobQuestionFactory(job=joined_job, asked_by=recruiter_profile))
    JobQuestionFactory(job=joined_job, asked_by=other, body="Their private question")
    JobQuestionFactory(
        job=joined_job,
        asked_by=other,
        answered_at=timezone.now(),
        hidden_at=timezone.now(),
        body="Hidden",
    )

    response = recruiter_client.get(_recruiter_url(joined_job))

    assert response.status_code == 200
    rows = response.json()
    assert isinstance(rows, list)
    assert {row["id"]: row["mine"] for row in rows} == {
        str(answered.pk): False,
        str(mine.pk): True,
    }
    assert set(rows[0]) == {"id", "body", "answer", "answered_at", "created_at", "mine"}


def test_recruiter_cannot_open_job_not_offered(
    recruiter_client: APIClient, company: object, user: User
) -> None:
    hidden = cast(Job, JobFactory(status=JobStatus.PENDING))

    assert recruiter_client.get(_recruiter_url(hidden)).status_code == 404
    assert recruiter_client.post(_recruiter_url(hidden), {"body": "Hello?"}).status_code == 404


def test_ask_returns_201_and_400_detail_when_closed(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile, joined_job: Job
) -> None:
    created = recruiter_client.post(_recruiter_url(joined_job), {"body": "  Is it hybrid?  "})

    assert created.status_code == 201
    body = created.json()
    assert body["body"] == "Is it hybrid?"
    assert body["mine"] is True
    assert body["answer"] == ""
    assert body["answered_at"] is None
    assert JobQuestion.objects.get(pk=body["id"]).asked_by == recruiter_profile

    Job.objects.filter(pk=joined_job.pk).update(status=JobStatus.CLOSED)
    closed = recruiter_client.post(_recruiter_url(joined_job), {"body": "Still open?"})

    assert closed.status_code == 400
    assert closed.json() == {"detail": JOB_NOT_TAKING_QUESTIONS}
    # A closed job is still readable to the recruiter who joined it.
    assert recruiter_client.get(_recruiter_url(joined_job)).status_code == 200


def test_ask_rejects_too_many_open_and_bad_bodies(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile, joined_job: Job
) -> None:
    assert recruiter_client.post(_recruiter_url(joined_job), {"body": "   "}).status_code == 400
    assert (
        recruiter_client.post(_recruiter_url(joined_job), {"body": "x" * 1001}).status_code == 400
    )
    for _ in range(5):
        JobQuestionFactory(job=joined_job, asked_by=recruiter_profile)

    response = recruiter_client.post(_recruiter_url(joined_job), {"body": "One more?"})

    assert response.status_code == 400
    assert response.json() == {"detail": TOO_MANY_OPEN}


def test_company_user_cannot_use_the_recruiter_endpoints(
    client_for: ClientFor, company_ta: User, job: Job
) -> None:
    assert client_for(company_ta).get(_recruiter_url(job)).status_code == 403


def test_company_lists_with_nickname(
    client_for: ClientFor, company_ta: User, job: Job, recruiter_profile: RecruiterProfile
) -> None:
    other = cast(RecruiterProfile, RecruiterProfileFactory(nickname="Atlas Hunters"))
    waiting = cast(JobQuestion, JobQuestionFactory(job=job, asked_by=recruiter_profile))
    answered = _answered(job, other)
    answered.answered_by = company_ta
    answered.save(update_fields=["answered_by"])
    JobQuestionFactory(job=job, asked_by=other, hidden_at=timezone.now())

    response = client_for(company_ta).get(_company_url(job))

    assert response.status_code == 200
    rows = response.json()
    # Waiting first, then the answered.
    assert [row["id"] for row in rows] == [str(waiting.pk), str(answered.pk)]
    assert rows[0]["recruiter_nickname"] == "NorthStar Talent"
    assert rows[0]["answered_by_name"] is None
    assert rows[1]["recruiter_nickname"] == "Atlas Hunters"
    assert rows[1]["answered_by_name"] == company_ta.full_name
    assert set(rows[0]) == {
        "id",
        "body",
        "answer",
        "answered_at",
        "created_at",
        "recruiter_nickname",
        "answered_by_name",
    }


def test_answer_and_hide(
    client_for: ClientFor,
    company_admin: User,
    job: Job,
    recruiter_profile: RecruiterProfile,
    mailoutbox: list[object],
) -> None:
    question = cast(JobQuestion, JobQuestionFactory(job=job, asked_by=recruiter_profile))
    client = client_for(company_admin)
    url = f"/api/v1/job-questions/{question.pk}/answer"

    assert client.post(url, {"answer": ""}).status_code == 400
    assert client.post(url, {"answer": "x" * 2001}).status_code == 400

    answered = client.post(url, {"answer": "  Yes, it is hybrid.  "})

    assert answered.status_code == 200
    body = answered.json()
    assert body["answer"] == "Yes, it is hybrid."
    assert body["answered_by_name"] == company_admin.full_name
    assert body["answered_at"] is not None
    assert body["recruiter_nickname"] == "NorthStar Talent"
    assert len(mailoutbox) == 1

    # Editing the answer keeps the first answer time and sends no second email.
    first = body["answered_at"]
    edited = client.post(url, {"answer": "Hybrid, two days in the office."})
    assert edited.status_code == 200
    assert edited.json()["answered_at"] == first
    assert len(mailoutbox) == 1

    deleted = client.delete(f"/api/v1/job-questions/{question.pk}")

    assert deleted.status_code == 204
    question.refresh_from_db()
    assert question.hidden_at is not None
    assert client.get(_company_url(job)).json() == []


def test_hidden_question_cannot_be_answered_or_hidden_again(
    client_for: ClientFor, company_admin: User, job: Job, recruiter_profile: RecruiterProfile
) -> None:
    question = cast(
        JobQuestion,
        JobQuestionFactory(job=job, asked_by=recruiter_profile, hidden_at=timezone.now()),
    )
    client = client_for(company_admin)

    assert (
        client.post(f"/api/v1/job-questions/{question.pk}/answer", {"answer": "Hi"}).status_code
        == 404
    )
    assert client.delete(f"/api/v1/job-questions/{question.pk}").status_code == 404
    question.refresh_from_db()
    assert question.answered_at is None


def test_member_outside_team_gets_404(team_world: TeamWorld) -> None:
    question = cast(
        JobQuestion, JobQuestionFactory(job=team_world.job_a, asked_by=team_world.recruiter)
    )
    member = team_world.hm_a
    client = APIClient()
    client.force_login(member)
    answer_url = f"/api/v1/job-questions/{question.pk}/answer"

    # In the team a Hiring Manager sees the question; out of it they cannot even see it.
    assert client.get(_company_url(team_world.job_a)).status_code == 200
    move_to_another_team(member)

    assert client.get(_company_url(team_world.job_a)).status_code == 404
    assert client.post(answer_url, {"answer": "Hi"}).status_code == 404
    assert client.delete(f"/api/v1/job-questions/{question.pk}").status_code == 404
    question.refresh_from_db()
    assert question.answered_at is None
    assert question.hidden_at is None


def test_viewer_without_edit_rights_cannot_answer_or_hide(team_world: TeamWorld) -> None:
    question = cast(
        JobQuestion, JobQuestionFactory(job=team_world.job_a, asked_by=team_world.recruiter)
    )
    answer_url = f"/api/v1/job-questions/{question.pk}/answer"
    detail_url = f"/api/v1/job-questions/{question.pk}"

    # Both see the job, but neither may edit it: a Hiring Manager, and a TA who may only draft.
    for viewer in (team_world.hm_a, team_world.ta_draft):
        client = APIClient()
        client.force_login(viewer)
        assert client.get(_company_url(team_world.job_a)).status_code == 200
        assert client.post(answer_url, {"answer": "Hi"}).status_code == 403
        assert client.delete(detail_url).status_code == 403
    question.refresh_from_db()
    assert question.answered_at is None
    assert question.hidden_at is None

    # An admin and the publishing creator may.
    admin = APIClient()
    admin.force_login(team_world.admin)
    assert admin.post(answer_url, {"answer": "Hi"}).status_code == 200
    creator = APIClient()
    creator.force_login(team_world.ta_publish)
    assert creator.delete(detail_url).status_code == 204


def test_another_company_gets_404(
    client_for: ClientFor,
    other_company_admin: User,
    job: Job,
    recruiter_profile: RecruiterProfile,
) -> None:
    question = cast(JobQuestion, JobQuestionFactory(job=job, asked_by=recruiter_profile))
    client = client_for(other_company_admin)

    assert client.get(_company_url(job)).status_code == 404
    assert (
        client.post(f"/api/v1/job-questions/{question.pk}/answer", {"answer": "Hi"}).status_code
        == 404
    )


def test_a_recruiter_cannot_answer(
    recruiter_client: APIClient, job: Job, recruiter_profile: RecruiterProfile
) -> None:
    question = cast(JobQuestion, JobQuestionFactory(job=job, asked_by=recruiter_profile))

    response = recruiter_client.post(
        f"/api/v1/job-questions/{question.pk}/answer", {"answer": "Self-served"}
    )

    assert response.status_code == 404
