from typing import Any
from unittest.mock import ANY

import pytest
from guardian.shortcuts import remove_perm

from apps.accounts.models import User
from apps.candidates.models import Stage
from apps.companies.models import CompanyRole
from apps.companies.tests.factories import CompanyMembershipFactory
from apps.hiring.models import ApprovalRequest, ApprovalStatus
from apps.hiring.tests.conftest import at_stage

pytestmark = pytest.mark.django_db

WAITING = "/api/v1/approval-requests/waiting"


def detail(client: Any, application: Any) -> Any:
    return client.get(f"/api/v1/applications/{application.pk}").json()


def ask(client: Any, application: Any, approver: Any, note: str = "Skip ahead?") -> Any:
    return client.post(
        f"/api/v1/applications/{application.pk}/approval-requests",
        {"approver_id": str(approver.pk), "note": note},
        format="json",
    )


def decide(client: Any, approval_id: str, approve: bool, note: str = "") -> Any:
    return client.post(
        f"/api/v1/approval-requests/{approval_id}/decision",
        {"approve": approve, "note": note},
        format="json",
    )


def test_approvers_are_teammates_who_can_see_the_job(
    ta_client: Any, application: Any, company: Any, company_admin: Any, granted_hm: Any
) -> None:
    CompanyMembershipFactory(company=company, role=CompanyRole.HIRING_MANAGER)
    rows = ta_client.get(f"/api/v1/applications/{application.pk}/approvers").json()
    assert {row["id"] for row in rows} == {str(company_admin.pk), str(granted_hm.pk)}
    assert {row["role"] for row in rows} == {"admin", "hiring_manager"}


def test_a_request_shows_as_pending_to_everyone(
    ta_client: Any, admin_client: Any, application: Any, company_admin: Any, company_ta: Any
) -> None:
    response = ask(ta_client, application, company_admin)
    assert response.status_code == 201
    body = response.json()
    pending = body["pending_approval"]
    assert pending["approver"] == {
        "id": str(company_admin.pk),
        "full_name": company_admin.full_name,
    }
    assert pending["requested_by_name"] == company_ta.full_name
    assert pending["note"] == "Skip ahead?"
    assert pending["can_decide"] is False
    assert "request_approval" not in body["actions"]
    assert detail(admin_client, application)["pending_approval"]["can_decide"] is True


def test_cannot_ask_yourself_or_someone_outside_the_job(
    ta_client: Any, application: Any, company_ta: Any, company_hm: Any, other_company_admin: Any
) -> None:
    for person in (company_ta, company_hm, other_company_admin):
        response = ask(ta_client, application, person)
        assert response.status_code == 400
        assert response.json()["approver_id"] == ["Pick a teammate who can see this job."]


def test_a_second_request_is_409(ta_client: Any, application: Any, company_admin: Any) -> None:
    ask(ta_client, application, company_admin)
    response = ask(ta_client, application, company_admin)
    assert response.status_code == 409
    assert response.json()["detail"] == "An approval request is already waiting for this candidate."


def test_no_requests_from_interview(ta_client: Any, application: Any, company_admin: Any) -> None:
    assert ask(ta_client, at_stage(application, Stage.INTERVIEW), company_admin).status_code == 409


def test_approve_moves_the_candidate_on(
    ta_client: Any, admin_client: Any, application: Any, company_admin: Any
) -> None:
    approval_id = ask(ta_client, application, company_admin).json()["pending_approval"]["id"]
    response = decide(admin_client, approval_id, True, "Go ahead")
    assert response.status_code == 200
    assert (response.json()["stage"], response.json()["pending_approval"]) == ("screen", None)
    assert ApprovalRequest.objects.get(pk=approval_id).decision_note == "Go ahead"


def test_decline_needs_a_reason_and_keeps_the_stage(
    ta_client: Any, admin_client: Any, application: Any, company_admin: Any
) -> None:
    approval_id = ask(ta_client, application, company_admin).json()["pending_approval"]["id"]
    refused = decide(admin_client, approval_id, False)
    assert refused.status_code == 400
    assert refused.json()["note"] == ["Say why you are declining."]
    response = decide(admin_client, approval_id, False, "Not senior enough yet")
    assert response.status_code == 200
    assert (response.json()["stage"], response.json()["pending_approval"]) == ("new", None)
    assert ApprovalRequest.objects.get(pk=approval_id).status == ApprovalStatus.DECLINED


def test_only_the_approver_decides(
    ta_client: Any,
    client_for: Any,
    application: Any,
    company_admin: Any,
    granted_hm: Any,
    other_company_admin: Any,
) -> None:
    approval_id = ask(ta_client, application, company_admin).json()["pending_approval"]["id"]
    teammate = decide(client_for(granted_hm), approval_id, True)
    assert teammate.status_code == 403
    assert (
        teammate.json()["detail"] == f"Only {company_admin.full_name} can decide on this request."
    )
    assert decide(client_for(other_company_admin), approval_id, True).status_code == 404


def test_deciding_twice_is_409(
    ta_client: Any, admin_client: Any, application: Any, company_admin: Any
) -> None:
    approval_id = ask(ta_client, application, company_admin).json()["pending_approval"]["id"]
    decide(admin_client, approval_id, False, "No")
    assert decide(admin_client, approval_id, True).status_code == 409


def test_approver_who_lost_access_cannot_decide(
    ta_client: Any, client_for: Any, application: Any, granted_hm: Any, job: Any
) -> None:
    approval_id = ask(ta_client, application, granted_hm).json()["pending_approval"]["id"]
    remove_perm("jobs.view_job", granted_hm, job)
    assert decide(client_for(granted_hm), approval_id, True).status_code == 404
    rows = ta_client.get(f"/api/v1/applications/{application.pk}/approvers").json()
    assert str(granted_hm.pk) not in {row["id"] for row in rows}


def test_waiting_list_shows_requests_for_me_only(
    ta_client: Any, admin_client: Any, application: Any, company_admin: Any
) -> None:
    ask(ta_client, application, company_admin, note="Quick yes?")
    rows = admin_client.get(WAITING).json()
    assert len(rows) == 1
    assert rows[0]["application"]["id"] == str(application.pk)
    assert rows[0]["application"]["candidate_name"] == "Phạm Minh Quân"
    assert rows[0]["application"]["job"]["title"] == "Senior Backend Engineer"
    assert rows[0]["note"] == "Quick yes?"
    assert ta_client.get(WAITING).json() == []


def test_moving_the_stage_cancels_the_request(
    ta_client: Any, admin_client: Any, application: Any, company_admin: Any
) -> None:
    approval_id = ask(ta_client, application, company_admin).json()["pending_approval"]["id"]
    ta_client.post(f"/api/v1/applications/{application.pk}/move-forward")
    assert detail(admin_client, application)["pending_approval"] is None
    assert ApprovalRequest.objects.get(pk=approval_id).status == ApprovalStatus.CANCELLED
    assert admin_client.get(WAITING).json() == []


def test_a_request_to_someone_who_lost_access_stops_blocking(
    ta_client: Any, application: Any, company_admin: Any, granted_hm: Any, job: Any
) -> None:
    stuck = ask(ta_client, application, granted_hm).json()["pending_approval"]["id"]
    remove_perm("jobs.view_job", granted_hm, job)
    body = detail(ta_client, application)
    assert body["pending_approval"] is None
    assert "request_approval" in body["actions"]
    assert ask(ta_client, application, company_admin).status_code == 201
    assert ApprovalRequest.objects.get(pk=stuck).status == ApprovalStatus.CANCELLED


def test_a_request_to_a_deactivated_teammate_stops_blocking(
    ta_client: Any, application: Any, granted_hm: Any
) -> None:
    ask(ta_client, application, granted_hm)
    User.objects.filter(pk=granted_hm.pk).update(is_active=False)
    body = detail(ta_client, application)
    assert body["pending_approval"] is None
    assert "request_approval" in body["actions"]


def test_the_team_sees_why_a_request_was_declined(
    ta_client: Any, admin_client: Any, application: Any, company_admin: Any
) -> None:
    approval_id = ask(ta_client, application, company_admin).json()["pending_approval"]["id"]
    assert detail(ta_client, application)["last_decline"] is None
    decide(admin_client, approval_id, False, "Not senior enough yet")
    assert detail(ta_client, application)["last_decline"] == {
        "id": approval_id,
        "approver_name": company_admin.full_name,
        "reason": "Not senior enough yet",
        "decided_at": ANY,
    }
    # Asking again replaces it; moving the candidate on retires it.
    again = ask(ta_client, application, company_admin)
    assert again.json()["last_decline"] is None
    ta_client.post(f"/api/v1/applications/{application.pk}/move-forward")
    assert detail(ta_client, application)["last_decline"] is None
