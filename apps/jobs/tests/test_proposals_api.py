from collections.abc import Callable
from typing import Any, cast

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.tests.factories import OfficeFactory
from apps.jobs.models import ChangeKind, Job, JobChangeRequest, JobStatus
from apps.jobs.proposals import propose_change
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db

ClientFor = Callable[[User], APIClient]


def _job(world: TeamWorld) -> Job:
    return cast(
        Job,
        JobFactory(
            company=world.company,
            team=world.team_a,
            created_by=world.ta_draft,
            status=JobStatus.ACTIVE,
            role_type="new_position",
            working_hours="40h_mon_fri",
            languages=["en"],
        ),
    )


def _request(world: TeamWorld, job: Job | None = None, **kw: Any) -> JobChangeRequest:
    return propose_change(
        job or _job(world),
        actor=world.ta_draft,
        kind=kw.pop("kind", ChangeKind.EDIT),
        changes=kw.pop("changes", {"title": "Renamed"}),
    )


def test_create_and_shape(team_world: TeamWorld, client_for: ClientFor) -> None:
    job = _job(team_world)
    response = client_for(team_world.ta_draft).post(
        f"/api/v1/jobs/{job.pk}/change-requests",
        {"kind": "edit", "changes": {"title": "Renamed", "intent": "post"}},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["changes"] == {"title": "Renamed"}
    assert body["current"] == {"title": job.title}
    assert body["job"] == {"id": str(job.pk), "title": job.title}
    assert body["requested_by"]["id"] == str(team_world.ta_draft.pk)
    assert body["reviewed_by"] is None


def test_create_invalid_is_400_and_forbidden_is_403(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    job = _job(team_world)
    url = f"/api/v1/jobs/{job.pk}/change-requests"
    ta = client_for(team_world.ta_draft)
    assert ta.post(url, {"kind": "edit", "changes": {}}, format="json").status_code == 400
    assert ta.post(url, {"kind": "nope"}, format="json").status_code == 400
    assert (
        client_for(team_world.admin).post(url, {"kind": "close"}, format="json").status_code == 403
    )
    assert (
        client_for(team_world.hm_b).post(url, {"kind": "close"}, format="json").status_code == 404
    )


def test_company_change_requests_admin_only(team_world: TeamWorld, client_for: ClientFor) -> None:
    request = _request(team_world)
    url = "/api/v1/company/change-requests"
    for member in (team_world.ta_draft, team_world.hm_a):
        assert client_for(member).get(url).status_code == 403
    response = client_for(team_world.admin).get(url)
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["results"]] == [str(request.pk)]


def test_requester_sees_own_request_admin_sees_all(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    request = _request(team_world)
    url = f"/api/v1/jobs/{request.job_id}/change-requests"
    for member, expected in (
        (team_world.ta_draft, 1),
        (team_world.admin, 1),
        (team_world.hm_a, 0),
        (team_world.ta_publish, 0),
    ):
        response = client_for(member).get(url)
        assert response.status_code == 200
        assert len(response.json()["results"]) == expected, member.email


def test_other_team_request_is_404(team_world: TeamWorld, client_for: ClientFor) -> None:
    request = _request(team_world)
    for member in (team_world.hm_a, team_world.ta_publish, team_world.hm_b):
        for action in ("approve", "reject", "withdraw"):
            response = client_for(member).post(
                f"/api/v1/change-requests/{request.pk}/{action}", {"note": "x"}, format="json"
            )
            assert response.status_code == 404, (member.email, action)


def test_decisions_over_http(team_world: TeamWorld, client_for: ClientFor) -> None:
    request = _request(team_world)
    base = f"/api/v1/change-requests/{request.pk}"
    # The requester may not approve their own; the admin may not withdraw it.
    assert client_for(team_world.ta_draft).post(f"{base}/approve").status_code == 403
    assert client_for(team_world.admin).post(f"{base}/withdraw").status_code == 403
    admin = client_for(team_world.admin)
    assert admin.post(f"{base}/reject", {}, format="json").status_code == 400
    approved = admin.post(f"{base}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert admin.post(f"{base}/approve").status_code == 400
    request.job.refresh_from_db()
    assert request.job.title == "Renamed"


def test_reject_and_withdraw_over_http(team_world: TeamWorld, client_for: ClientFor) -> None:
    request = _request(team_world)
    rejected = client_for(team_world.admin).post(
        f"/api/v1/change-requests/{request.pk}/reject", {"note": "No"}, format="json"
    )
    assert rejected.status_code == 200
    assert rejected.json()["review_note"] == "No"
    other = _request(team_world)
    withdrawn = client_for(team_world.ta_draft).post(f"/api/v1/change-requests/{other.pk}/withdraw")
    assert withdrawn.json()["status"] == "withdrawn"


def test_job_list_counts_pending_changes_per_viewer(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    job = _job(team_world)
    _request(team_world, job)

    def counts(member: User) -> dict[str, int]:
        rows = client_for(member).get("/api/v1/jobs").json()["results"]
        return {row["id"]: row["pending_changes"] for row in rows}

    assert counts(team_world.admin)[str(job.pk)] == 1
    assert counts(team_world.ta_draft)[str(job.pk)] == 1
    assert counts(team_world.hm_a)[str(job.pk)] == 0
    assert counts(team_world.ta_publish)[str(job.pk)] == 0


def test_home_shows_change_request_action_to_admin_only(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    request = _request(team_world, kind=ChangeKind.CLOSE, changes={})
    admin_actions = client_for(team_world.admin).get("/api/v1/company/home").json()["actions"]
    mine = [a for a in admin_actions if a["kind"] == "change_request"]
    assert len(mine) == 1
    assert mine[0]["href"] == f"/jobs/{request.job_id}"
    assert mine[0]["title"] == "Ta Draft proposed closing this job"
    assert mine[0]["proposal_kind"] == ChangeKind.CLOSE
    assert mine[0]["proposed_by"] == "Ta Draft"
    assert mine[0]["detail"] == request.job.title
    ta_actions = client_for(team_world.ta_draft).get("/api/v1/company/home").json()["actions"]
    assert not [a for a in ta_actions if a["kind"] == "change_request"]


@pytest.mark.parametrize(
    ("kind", "title"),
    [
        (ChangeKind.EDIT, "Ta Draft proposed changes to this job"),
        (ChangeKind.CLOSE, "Ta Draft proposed closing this job"),
        (ChangeKind.REOPEN, "Ta Draft proposed reopening this job"),
        (ChangeKind.DELETE, "Ta Draft proposed deleting this job"),
    ],
)
def test_home_titles_each_proposal_kind_as_a_sentence(
    team_world: TeamWorld, client_for: ClientFor, kind: str, title: str
) -> None:
    JobChangeRequest.objects.create(
        job=_job(team_world), requested_by=team_world.ta_draft, kind=kind
    )

    actions = client_for(team_world.admin).get("/api/v1/company/home").json()["actions"]

    assert [a["title"] for a in actions if a["kind"] == "change_request"] == [title]


def test_null_current_values_do_not_break_the_inbox(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    job = _job(team_world)
    Job.objects.filter(pk=job.pk).update(
        interview_rounds_count=None, salary_min=None, salary_max=None
    )
    response = client_for(team_world.ta_draft).post(
        f"/api/v1/jobs/{job.pk}/change-requests",
        {"kind": "edit", "changes": {"interview_rounds_count": 3, "salary_min": 100}},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["current"] == {"interview_rounds_count": None, "salary_min": None}
    inbox = client_for(team_world.admin).get("/api/v1/company/change-requests")
    assert inbox.status_code == 200
    assert inbox.json()["results"][0]["current"]["salary_min"] is None


def test_exact_error_sentences(team_world: TeamWorld, client_for: ClientFor) -> None:
    job = _job(team_world)
    ta = client_for(team_world.ta_draft)
    empty = ta.post(
        f"/api/v1/jobs/{job.pk}/change-requests", {"kind": "edit", "changes": {}}, format="json"
    )
    assert empty.json() == {"changes": ["Change at least one field of the job."]}
    request = _request(team_world, job)
    base = f"/api/v1/change-requests/{request.pk}"
    assert ta.post(f"{base}/approve").json()["detail"] == "You can't do that on this job."
    assert (
        client_for(team_world.hm_a).get("/api/v1/company/change-requests").json()["detail"]
        == "Only a company admin can see every proposal."
    )
    admin = client_for(team_world.admin)
    admin.post(f"{base}/approve")
    assert admin.post(f"{base}/approve").json()["detail"] == "This proposal was already decided."


def test_failed_revalidation_is_400_on_office_and_stays_pending(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    job = _job(team_world)
    office = OfficeFactory(company=team_world.company, position=1)
    request = _request(team_world, job, changes={"office": str(office.pk)})
    office.delete()
    response = client_for(team_world.admin).post(f"/api/v1/change-requests/{request.pk}/approve")
    assert response.status_code == 400
    assert "office" in response.json()
    request.refresh_from_db()
    assert request.status == "pending"


def test_only_pending_requests_are_counted(team_world: TeamWorld, client_for: ClientFor) -> None:
    job = _job(team_world)
    first = _request(team_world, job)
    client_for(team_world.ta_draft).post(f"/api/v1/change-requests/{first.pk}/withdraw")
    second = _request(team_world, job)
    client_for(team_world.admin).post(f"/api/v1/change-requests/{second.pk}/approve")
    rows = client_for(team_world.admin).get("/api/v1/jobs").json()["results"]
    assert {row["id"]: row["pending_changes"] for row in rows}[str(job.pk)] == 0


def test_change_requests_status_filter(team_world: TeamWorld, client_for: ClientFor) -> None:
    job = _job(team_world)
    rejected = _request(team_world, job)
    client_for(team_world.admin).post(
        f"/api/v1/change-requests/{rejected.pk}/reject", {"note": "No"}, format="json"
    )
    pending = _request(team_world, job, changes={"title": "Again"})
    url = f"/api/v1/jobs/{job.pk}/change-requests"
    requester = client_for(team_world.ta_draft)

    default = requester.get(url).json()["results"]
    assert [r["id"] for r in default] == [str(pending.pk)]

    only_rejected = requester.get(url, {"status": "rejected"}).json()["results"]
    assert [r["id"] for r in only_rejected] == [str(rejected.pk)]

    everything = requester.get(url, {"status": "all"}).json()["results"]
    assert [r["id"] for r in everything] == [str(pending.pk), str(rejected.pk)]

    unknown = requester.get(url, {"status": "bogus"})
    assert unknown.status_code == 400
    assert unknown.json()["detail"] == "Unknown status."

    other = client_for(team_world.ta_publish)
    assert other.get(url, {"status": "all"}).json()["results"] == []


def test_status_filter_for_admin_and_other_states(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    job = _job(team_world)
    approved = _request(team_world, job)
    admin = client_for(team_world.admin)
    assert admin.post(f"/api/v1/change-requests/{approved.pk}/approve").status_code == 200
    withdrawn = propose_change(job, actor=team_world.ta_draft, kind=ChangeKind.CLOSE, changes=None)
    assert (
        client_for(team_world.ta_draft)
        .post(f"/api/v1/change-requests/{withdrawn.pk}/withdraw")
        .status_code
        == 200
    )
    pending = propose_change(job, actor=team_world.ta_draft, kind=ChangeKind.CLOSE, changes=None)
    url = f"/api/v1/jobs/{job.pk}/change-requests"

    def ids(**params: str) -> list[str]:
        return [r["id"] for r in admin.get(url, params).json()["results"]]

    assert ids(status="approved") == [str(approved.pk)]
    assert ids(status="withdrawn") == [str(withdrawn.pk)]
    assert ids() == [str(pending.pk)]
    assert ids(status="all") == [str(pending.pk), str(withdrawn.pk), str(approved.pk)]
