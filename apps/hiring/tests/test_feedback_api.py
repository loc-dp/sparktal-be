import uuid
from datetime import timedelta
from typing import Any, cast

import pytest

from apps.candidates.models import Stage
from apps.hiring.models import Feedback, Interview
from apps.hiring.tests.conftest import at_stage
from apps.hiring.tests.factories import CandidateNoteFactory, FeedbackFactory, InterviewFactory

pytestmark = pytest.mark.django_db


def test_first_put_creates_and_the_next_updates(
    ta_client: Any, application: Any, company_ta: Any
) -> None:
    interview = InterviewFactory(application=at_stage(application, Stage.INTERVIEW), round=1)
    url = f"/api/v1/interviews/{interview.pk}/feedback"
    first = ta_client.put(url, {"verdict": "yes", "body": "Good on Kafka"}, format="json")
    assert first.status_code == 200
    assert first.json()["round"] == 1
    assert first.json()["interview_id"] == str(interview.pk)
    assert first.json()["author_name"] == company_ta.full_name
    assert first.json()["is_mine"] is True
    second = ta_client.put(url, {"verdict": "strong_yes"}, format="json")
    assert second.json()["id"] == first.json()["id"]
    assert (second.json()["verdict"], second.json()["body"]) == ("strong_yes", "")
    assert Feedback.objects.count() == 1


def test_everyone_on_the_job_reads_all_feedback_by_round(
    admin_client: Any, application: Any, company_ta: Any, company_admin: Any
) -> None:
    round_one = cast(Interview, InterviewFactory(application=application, round=1))
    round_two = InterviewFactory(
        application=application,
        round=2,
        starts_at=round_one.starts_at + timedelta(days=3),
        ends_at=round_one.ends_at + timedelta(days=3),
    )
    FeedbackFactory(interview=round_two, author=company_admin, verdict="no")
    FeedbackFactory(interview=round_one, author=company_ta, verdict="yes")
    rows = admin_client.get(f"/api/v1/applications/{application.pk}/feedback").json()
    assert [(row["round"], row["verdict"], row["is_mine"]) for row in rows] == [
        (1, "yes", False),
        (2, "no", True),
    ]


def test_unknown_verdict_is_400(ta_client: Any, application: Any) -> None:
    interview = InterviewFactory(application=application)
    response = ta_client.put(
        f"/api/v1/interviews/{interview.pk}/feedback", {"verdict": "maybe"}, format="json"
    )
    assert response.status_code == 400
    assert "verdict" in response.json()


@pytest.mark.parametrize("who", ["company_hm", "other_company_admin"])
def test_feedback_is_404_outside_the_job(
    who: str, request: pytest.FixtureRequest, client_for: Any, application: Any
) -> None:
    interview = InterviewFactory(application=application)
    client = client_for(request.getfixturevalue(who))
    assert client.get(f"/api/v1/applications/{application.pk}/feedback").status_code == 404
    response = client.put(
        f"/api/v1/interviews/{interview.pk}/feedback", {"verdict": "yes"}, format="json"
    )
    assert response.status_code == 404


def test_recruiter_cannot_reach_company_only_endpoints(
    client_for: Any, recruiter_user: Any, application: Any, company_ta: Any
) -> None:
    interview = InterviewFactory(application=application)
    note = CandidateNoteFactory(application=application, author=company_ta)
    client = client_for(recruiter_user)
    base = f"/api/v1/applications/{application.pk}"
    assert client.get(base).status_code == 404
    assert client.get(f"{base}/notes").status_code == 404
    assert client.post(f"{base}/notes", {"body": "x"}, format="json").status_code == 404
    assert client.delete(f"/api/v1/notes/{note.pk}").status_code == 404
    assert client.get(f"{base}/feedback").status_code == 404
    assert (
        client.put(
            f"/api/v1/interviews/{interview.pk}/feedback", {"verdict": "yes"}, format="json"
        ).status_code
        == 404
    )
    assert client.get(f"{base}/approvers").status_code == 404
    assert (
        client.post(
            f"{base}/approval-requests", {"approver_id": str(uuid.uuid4())}, format="json"
        ).status_code
        == 404
    )
    assert client.post(f"{base}/move-forward").status_code == 404
