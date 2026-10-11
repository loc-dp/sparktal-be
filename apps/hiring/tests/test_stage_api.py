from typing import Any

import pytest

from apps.candidates.models import Stage
from apps.hiring.tests.conftest import at_stage

pytestmark = pytest.mark.django_db

SLOT = {
    "starts_at": "2026-09-22T09:00:00+07:00",
    "ends_at": "2026-09-22T10:00:00+07:00",
    "format": "online",
    "location": "https://meet.google.com/abc-defg-hij",
}


def post(client: Any, application: Any, action: str, data: dict[str, Any] | None = None) -> Any:
    return client.post(f"/api/v1/applications/{application.pk}/{action}", data or {}, format="json")


def test_move_forward_answers_with_the_fresh_detail(
    ta_client: Any, application: Any, company_ta: Any
) -> None:
    response = post(ta_client, application, "move-forward")
    assert response.status_code == 200
    body = response.json()
    assert body["stage"] == "screen"
    assert body["actions"] == ["set_interview", "request_approval", "archive"]
    assert body["history"][-1]["stage"] == "screen"
    assert body["history"][-1]["by"] == company_ta.full_name


def test_a_stale_command_answers_409(ta_client: Any, application: Any) -> None:
    post(ta_client, application, "move-forward")
    response = post(ta_client, application, "move-forward")
    assert response.status_code == 409
    assert "moved on" in response.json()["detail"]


def test_set_interview_from_screen(ta_client: Any, application: Any) -> None:
    at_stage(application, Stage.SCREEN)
    response = post(ta_client, application, "interviews", SLOT)
    assert response.status_code == 201
    body = response.json()
    assert body["stage"] == "interview"
    assert body["interviews"] == [
        {
            "id": body["interviews"][0]["id"],
            "round": 1,
            "starts_at": "2026-09-22T09:00:00+07:00",
            "ends_at": "2026-09-22T10:00:00+07:00",
            "format": "online",
            "location": "https://meet.google.com/abc-defg-hij",
        }
    ]
    assert body["actions"] == ["next_interview", "offer", "archive"]


def test_next_interview_adds_round_two(ta_client: Any, application: Any) -> None:
    at_stage(application, Stage.SCREEN)
    post(ta_client, application, "interviews", SLOT)
    later = {
        **SLOT,
        "starts_at": "2026-09-25T14:00:00+07:00",
        "ends_at": "2026-09-25T15:00:00+07:00",
    }
    body = post(ta_client, application, "interviews", later).json()
    assert [interview["round"] for interview in body["interviews"]] == [1, 2]


def test_interview_must_end_after_it_starts(ta_client: Any, application: Any) -> None:
    at_stage(application, Stage.SCREEN)
    backwards = {**SLOT, "ends_at": "2026-09-22T08:00:00+07:00"}
    response = post(ta_client, application, "interviews", backwards)
    assert response.status_code == 400
    assert response.json()["ends_at"] == ["The interview must end after it starts."]


def test_unknown_interview_format_is_400(ta_client: Any, application: Any) -> None:
    at_stage(application, Stage.SCREEN)
    response = post(ta_client, application, "interviews", {**SLOT, "format": "carrier pigeon"})
    assert response.status_code == 400
    assert "format" in response.json()


def test_set_interview_from_new_is_409(ta_client: Any, application: Any) -> None:
    assert post(ta_client, application, "interviews", SLOT).status_code == 409


def test_offer_then_hire(ta_client: Any, application: Any) -> None:
    at_stage(application, Stage.INTERVIEW)
    assert post(ta_client, application, "offer").json()["stage"] == "offer"
    body = post(ta_client, application, "hire").json()
    assert (body["stage"], body["actions"]) == ("onboarded", [])


def test_offer_from_new_is_409(ta_client: Any, application: Any) -> None:
    assert post(ta_client, application, "offer").status_code == 409


def test_archive_needs_a_reason(ta_client: Any, application: Any) -> None:
    response = post(ta_client, application, "archive", {"reason": "   "})
    assert response.status_code == 400
    assert response.json()["reason"] == ["Say why you are archiving this candidate."]


def test_archive_stores_the_reason(ta_client: Any, application: Any) -> None:
    body = post(ta_client, application, "archive", {"reason": "Took another offer"}).json()
    assert (body["stage"], body["rejected_reason"], body["actions"]) == (
        "rejected",
        "Took another offer",
        [],
    )


@pytest.mark.parametrize("action", ["move-forward", "offer", "hire", "archive", "interviews"])
@pytest.mark.parametrize("who", ["company_hm", "other_company_admin", "recruiter_user"])
def test_commands_are_404_outside_the_job(
    action: str, who: str, request: pytest.FixtureRequest, client_for: Any, application: Any
) -> None:
    client = client_for(request.getfixturevalue(who))
    response = client.post(
        f"/api/v1/applications/{application.pk}/{action}", {"reason": "x", **SLOT}, format="json"
    )
    assert response.status_code == 404
    application.refresh_from_db()
    assert application.stage == Stage.NEW
