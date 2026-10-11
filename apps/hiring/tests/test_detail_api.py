import uuid
from typing import Any

import pytest

from apps.candidates.models import Stage
from apps.hiring.cv import cv_kind
from apps.hiring.history import stage_history

pytestmark = pytest.mark.django_db


def url(application: Any) -> str:
    return f"/api/v1/applications/{application.pk}"


def test_ta_reads_the_application(ta_client: Any, application: Any) -> None:
    response = ta_client.get(url(application))
    assert response.status_code == 200
    body = response.json()
    assert body["job"] == {"id": str(application.job_id), "title": "Senior Backend Engineer"}
    assert body["candidate"]["full_name"] == "Phạm Minh Quân"
    assert body["candidate"]["languages"] == ["Vietnamese", "English"]
    assert body["candidate"]["location"] == "hcm"
    assert body["candidate"]["expected_salary"] == "50000000"
    assert body["recruiter"]["nickname"] == "NorthStar Talent"
    assert body["stage"] == "new"
    assert body["cv_kind"] == "pdf"
    assert body["motivation"] == "Wants to own a payments ledger."
    assert body["consultant_note"] == "Strong on Go and Kafka."
    assert body["interviews"] == []
    assert body["pending_approval"] is None
    assert body["first_submitted_at"] is None
    assert body["actions"] == ["move_forward", "request_approval", "archive"]
    assert [entry["stage"] for entry in body["history"]] == ["new"]


def test_company_never_sees_how_to_reach_the_candidate(ta_client: Any, application: Any) -> None:
    candidate = ta_client.get(url(application)).json()["candidate"]
    assert not {"email", "phone", "dob", "id", "notes"} & set(candidate)


@pytest.mark.parametrize("who", ["company_admin", "granted_hm"])
def test_admin_and_a_granted_hiring_manager_can_read(
    who: str, request: pytest.FixtureRequest, client_for: Any, application: Any
) -> None:
    user = request.getfixturevalue(who)
    assert client_for(user).get(url(application)).status_code == 200


@pytest.mark.parametrize("who", ["company_hm", "other_company_admin", "recruiter_user"])
def test_everyone_else_gets_404(
    who: str, request: pytest.FixtureRequest, client_for: Any, application: Any
) -> None:
    user = request.getfixturevalue(who)
    assert client_for(user).get(url(application)).status_code == 404


def test_unknown_application_is_404(ta_client: Any) -> None:
    assert ta_client.get(f"/api/v1/applications/{uuid.uuid4()}").status_code == 404


def test_anonymous_is_turned_away(api_client: Any, application: Any) -> None:
    assert api_client.get(url(application)).status_code in (401, 403)


@pytest.mark.parametrize(
    ("filename", "kind"),
    [("CV.PDF", "pdf"), ("cv.docx", "docx"), ("cv.doc", "doc"), ("cv.txt", "other"), ("", "other")],
)
def test_cv_kind_reads_the_extension(filename: str, kind: str) -> None:
    assert cv_kind(filename) == kind


def test_history_lists_each_stage_once(application: Any) -> None:
    application.motivation = "Edited, same stage"
    application.save()
    application.move_to(Stage.SCREEN)
    entries = stage_history(application)
    assert [entry["stage"] for entry in entries] == ["new", "screen"]
    assert entries[-1]["at"] == application.stage_changed_at
    assert entries[-1]["by"] is None
