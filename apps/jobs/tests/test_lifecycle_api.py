from collections.abc import Callable
from typing import Any, cast

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient, APIRequestFactory

from apps.accounts.models import User
from apps.companies.models import Team
from apps.companies.tests.factories import CompanyFactory, CompanyMembershipFactory, TeamFactory
from apps.jobs.access import NOT_YOURS
from apps.jobs.lifecycle import REQUIRED_MESSAGES, close_job, delete_job, post_job
from apps.jobs.models import ChangeKind, ChangeStatus, Job, JobStatus
from apps.jobs.proposals import propose_change
from apps.jobs.serializers import PRIVATE_JOB_TEAM_MESSAGE, JobSerializer
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db

ClientFor = Callable[[User], APIClient]

BRIEF = {"role_type": "new_position", "working_hours": "40h_mon_fri", "languages": ["en"]}


def _body(world: TeamWorld, **extra: Any) -> dict[str, Any]:
    return {
        "title": "Platform Engineer",
        "why_we_need_you": "We are growing the platform team.",
        "roles": "Run the platform",
        "qualifications": "5 years of ops",
        "work_model": "remote",
        **BRIEF,
        **extra,
    }


def _draft(world: TeamWorld, **fields: Any) -> Job:
    return cast(
        Job,
        JobFactory(
            company=world.company,
            team=world.team_a,
            **{
                "created_by": world.ta_draft,
                "status": JobStatus.DRAFT,
                "published_at": None,
                **BRIEF,
                **fields,
            },
        ),
    )


def test_create_draft_with_title_only(team_world: TeamWorld, client_for: ClientFor) -> None:
    response = client_for(team_world.ta_draft).post(
        "/api/v1/jobs", {"title": "Idea"}, format="json"
    )

    assert response.status_code == 201
    assert response.data["status"] == "draft"
    job = Job.objects.get(pk=response.data["id"])
    assert job.team_id == team_world.team_a.pk


def test_create_with_intent_post_needs_full_jd(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    before = Job.objects.count()

    response = client_for(team_world.admin).post(
        "/api/v1/jobs",
        {"title": "Idea", "intent": "post", "team": str(team_world.team_a.pk)},
        format="json",
    )

    assert response.status_code == 400
    assert "roles" in response.data
    assert Job.objects.count() == before


def test_create_with_intent_post_by_admin(team_world: TeamWorld, client_for: ClientFor) -> None:
    response = client_for(team_world.admin).post(
        "/api/v1/jobs",
        _body(team_world, intent="post", team=str(team_world.team_a.pk)),
        format="json",
    )

    assert response.status_code == 201
    assert response.data["status"] == "pending"


def test_create_with_intent_submit_by_ta_without_publish(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    response = client_for(team_world.ta_draft).post(
        "/api/v1/jobs", _body(team_world, intent="submit"), format="json"
    )

    assert response.status_code == 201
    assert response.data["status"] == "submitted"


def test_create_with_intent_post_by_ta_without_publish_is_403_and_rolled_back(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    before = Job.objects.count()

    response = client_for(team_world.ta_draft).post(
        "/api/v1/jobs", _body(team_world, intent="post"), format="json"
    )

    assert response.status_code == 403
    assert response.data["detail"] == NOT_YOURS
    assert Job.objects.count() == before


def test_ta_ignores_a_team_in_the_payload(team_world: TeamWorld, client_for: ClientFor) -> None:
    response = client_for(team_world.ta_draft).post(
        "/api/v1/jobs", {"title": "Idea", "team": str(team_world.team_b.pk)}, format="json"
    )

    assert response.status_code == 201
    assert Job.objects.get(pk=response.data["id"]).team_id == team_world.team_a.pk


def test_admin_must_pick_team(team_world: TeamWorld, client_for: ClientFor) -> None:
    client = client_for(team_world.admin)

    missing = client.post("/api/v1/jobs", {"title": "Idea"}, format="json")
    picked = client.post(
        "/api/v1/jobs", {"title": "Idea", "team": str(team_world.team_b.pk)}, format="json"
    )

    assert missing.status_code == 400
    assert missing.data == {"team": ["Pick the team this job belongs to."]}
    assert picked.status_code == 201
    assert Job.objects.get(pk=picked.data["id"]).team_id == team_world.team_b.pk


def test_admin_of_a_company_without_teams_needs_no_team(client_for: ClientFor) -> None:
    company = CompanyFactory()
    admin = CompanyMembershipFactory(company=company, role="admin", team=None).user

    response = client_for(admin).post("/api/v1/jobs", {"title": "Idea"}, format="json")

    assert response.status_code == 201
    assert response.data["team"] is None


def test_admin_cannot_link_a_team_of_another_company(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    foreign: Team = TeamFactory()

    response = client_for(team_world.admin).post(
        "/api/v1/jobs", {"title": "Idea", "team": str(foreign.pk)}, format="json"
    )

    assert response.status_code == 400
    assert "team" in response.data
    assert not Job.objects.filter(team=foreign).exists()


def test_hm_cannot_create_job(team_world: TeamWorld, client_for: ClientFor) -> None:
    response = client_for(team_world.hm_a).post("/api/v1/jobs", {"title": "Idea"}, format="json")

    assert response.status_code == 403


def test_validating_a_partial_payload_has_no_side_effects(team_world: TeamWorld) -> None:
    from rest_framework.test import APIRequestFactory

    from apps.jobs.serializers import JobSerializer

    job = _draft(team_world)
    request = APIRequestFactory().patch("/")
    request.user = team_world.ta_draft

    serializer = JobSerializer(
        job, data={"intent": "post", "roles": "x"}, partial=True, context={"request": request}
    )
    assert serializer.is_valid()

    job.refresh_from_db()
    assert job.status == JobStatus.DRAFT


def test_patch_saves_a_draft_with_partial_data(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    job = _draft(team_world)

    response = client_for(team_world.ta_draft).patch(
        f"/api/v1/jobs/{job.pk}", {"roles": ""}, format="json"
    )

    assert response.status_code == 200
    job.refresh_from_db()
    assert job.roles == ""


def test_patch_with_intent_submit(team_world: TeamWorld, client_for: ClientFor) -> None:
    job = _draft(team_world)

    response = client_for(team_world.ta_draft).patch(
        f"/api/v1/jobs/{job.pk}", {"intent": "submit"}, format="json"
    )

    assert response.status_code == 200
    assert response.data["status"] == "submitted"


def test_patch_posted_job_by_ta_without_publish_is_403(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    job = _draft(team_world, status=JobStatus.PENDING)

    response = client_for(team_world.ta_draft).patch(
        f"/api/v1/jobs/{job.pk}", {"title": "Changed"}, format="json"
    )

    assert response.status_code == 403
    assert response.data["detail"] == NOT_YOURS


def test_submit_withdraw_post_endpoints(team_world: TeamWorld, client_for: ClientFor) -> None:
    job = _draft(team_world)
    ta = client_for(team_world.ta_draft)
    admin = client_for(team_world.admin)

    assert ta.post(f"/api/v1/jobs/{job.pk}/submit").data["status"] == "submitted"
    assert ta.post(f"/api/v1/jobs/{job.pk}/withdraw").data["status"] == "draft"
    ta.post(f"/api/v1/jobs/{job.pk}/submit")
    response = admin.post(f"/api/v1/jobs/{job.pk}/post")

    assert response.status_code == 200
    assert response.data["status"] == "pending"


def test_submit_incomplete_job_is_400(team_world: TeamWorld, client_for: ClientFor) -> None:
    job = _draft(team_world, roles="")

    response = client_for(team_world.ta_draft).post(f"/api/v1/jobs/{job.pk}/submit")

    assert response.status_code == 400
    assert "roles" in response.data


def test_close_and_reopen_without_rights_is_403_not_400(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    job = _draft(team_world, status=JobStatus.SUBMITTED)
    client = client_for(team_world.ta_draft)

    for action in ("close", "reopen"):
        response = client.post(f"/api/v1/jobs/{job.pk}/{action}")
        assert response.status_code == 403
        assert response.data["detail"] == NOT_YOURS


def test_delete_endpoint(team_world: TeamWorld, client_for: ClientFor) -> None:
    job = _draft(team_world)
    client = client_for(team_world.ta_draft)

    assert client.delete(f"/api/v1/jobs/{job.pk}").status_code == 204
    assert client.get(f"/api/v1/jobs/{job.pk}").status_code == 404
    job.refresh_from_db()
    assert job.deleted_at is not None


def test_delete_endpoint_blocked_by_live_applications(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    client = client_for(team_world.admin)
    client.post(f"/api/v1/jobs/{team_world.job_a.pk}/close")

    response = client.delete(f"/api/v1/jobs/{team_world.job_a.pk}")

    assert response.status_code == 400
    assert response.data["detail"] == (
        "Close the job and finish or archive every candidate before deleting it."
    )


def test_status_filter_accepts_list(team_world: TeamWorld, client_for: ClientFor) -> None:
    draft = _draft(team_world)
    submitted = _draft(team_world, status=JobStatus.SUBMITTED)

    response = client_for(team_world.ta_draft).get("/api/v1/jobs?status=draft,submitted")

    ids = {row["id"] for row in response.data["results"]}
    assert ids == {str(draft.pk), str(submitted.pk)}
    assert client_for(team_world.ta_draft).get("/api/v1/jobs?status=nope").status_code == 400


def test_counts_always_carry_draft_and_submitted(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    counts = client_for(team_world.hm_a).get("/api/v1/jobs/counts").data
    assert counts == {"draft": 0, "submitted": 0, "pending": 0, "active": 1, "closed": 0}

    _draft(team_world)
    _draft(team_world, status=JobStatus.SUBMITTED)
    counts = client_for(team_world.ta_draft).get("/api/v1/jobs/counts").data
    assert (counts["draft"], counts["submitted"]) == (1, 1)


def test_job_payload_carries_permissions(team_world: TeamWorld, client_for: ClientFor) -> None:
    job = _draft(team_world)

    ta = client_for(team_world.ta_draft).get(f"/api/v1/jobs/{job.pk}").data
    admin = client_for(team_world.admin).get(f"/api/v1/jobs/{team_world.job_a.pk}").data

    assert ta["permissions"]["submit"] is True
    assert ta["permissions"]["post"] is False
    assert ta["can_edit"] is True
    assert admin["permissions"]["close"] is True
    assert admin["permissions"]["review_proposals"] is True
    assert len(ta["permissions"]) == 9


def test_list_rows_carry_team_name_and_no_permissions(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    client = client_for(team_world.admin)
    with CaptureQueriesContext(connection) as baseline:
        response = client.get("/api/v1/jobs")

    for _ in range(5):
        JobFactory(company=team_world.company, team=team_world.team_a)

    # The same queries for any number of rows: no per-row permission or team lookups.
    with CaptureQueriesContext(connection) as more_rows:
        response = client.get("/api/v1/jobs")
    assert len(more_rows) == len(baseline)

    row = next(r for r in response.data["results"] if r["id"] == str(team_world.job_a.pk))
    assert row["team_name"] == "Payments"
    assert "permissions" not in row


@pytest.mark.parametrize("intent", ["post", "submit"])
@pytest.mark.parametrize("status", [JobStatus.PENDING, JobStatus.ACTIVE])
def test_patch_intent_on_a_posted_job_is_403_and_changes_nothing(
    team_world: TeamWorld, client_for: ClientFor, intent: str, status: str
) -> None:
    job = _draft(team_world, status=status, title="Original")

    response = client_for(team_world.ta_draft).patch(
        f"/api/v1/jobs/{job.pk}", {"title": "Changed", "intent": intent}, format="json"
    )

    assert response.status_code == 403
    job.refresh_from_db()
    assert (job.title, job.status) == ("Original", status)


@pytest.mark.parametrize("who", ["admin", "ta_publish"])
def test_create_with_intent_submit_by_someone_who_can_post_is_403(
    team_world: TeamWorld, client_for: ClientFor, who: str
) -> None:
    before = Job.objects.count()

    response = client_for(getattr(team_world, who)).post(
        "/api/v1/jobs",
        _body(team_world, intent="submit", team=str(team_world.team_a.pk)),
        format="json",
    )

    assert response.status_code == 403
    assert Job.objects.count() == before


def test_withdraw_by_someone_else_is_refused(team_world: TeamWorld, client_for: ClientFor) -> None:
    job = _draft(team_world, status=JobStatus.SUBMITTED)

    teammate = client_for(team_world.ta_publish).post(f"/api/v1/jobs/{job.pk}/withdraw")
    other_team = client_for(team_world.hm_b).post(f"/api/v1/jobs/{job.pk}/withdraw")
    admin = client_for(team_world.admin).post(f"/api/v1/jobs/{job.pk}/withdraw")

    assert teammate.status_code == 404  # a submitted job is private to its creator's view
    assert other_team.status_code == 404
    assert admin.status_code == 403
    job.refresh_from_db()
    assert job.status == JobStatus.SUBMITTED


def test_ta_with_a_team_in_the_payload_never_gets_a_team_error(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    foreign: Team = TeamFactory()

    response = client_for(team_world.ta_draft).post(
        "/api/v1/jobs", {"title": "Idea", "team": str(foreign.pk)}, format="json"
    )

    assert response.status_code == 201
    assert Job.objects.get(pk=response.data["id"]).team_id == team_world.team_a.pk


def test_admin_without_teams_may_send_team_null(client_for: ClientFor) -> None:
    company = CompanyFactory()
    admin = CompanyMembershipFactory(company=company, role="admin", team=None).user
    client = client_for(admin)

    created = client.post("/api/v1/jobs", {"title": "Idea", "team": None}, format="json")
    patched = client.patch(f"/api/v1/jobs/{created.data['id']}", {"team": None}, format="json")

    assert created.status_code == 201
    assert patched.status_code == 200
    assert patched.data["team"] is None


def test_admin_with_teams_cannot_patch_team_to_null(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    client = client_for(team_world.admin)

    response = client.patch(f"/api/v1/jobs/{team_world.job_b.pk}", {"team": None}, format="json")
    untouched = client.patch(
        f"/api/v1/jobs/{team_world.job_b.pk}", {"note_for_recruiters": "x"}, format="json"
    )

    assert response.status_code == 400
    assert response.data == {"team": ["Pick the team this job belongs to."]}
    assert untouched.status_code == 200
    team_world.job_b.refresh_from_db()
    assert team_world.job_b.team_id == team_world.team_b.pk


# --- A PATCH writes onto the job as it is when it saves, not as it was read -------------------


def _read_then_patch(job: Job, user: User, data: dict[str, Any]) -> JobSerializer:
    """A PATCH that read and validated `job`, but has not saved yet."""
    request = APIRequestFactory().patch("/")
    request.user = user
    serializer = JobSerializer(
        Job.objects.get(pk=job.pk), data=data, partial=True, context={"request": request}
    )
    assert serializer.is_valid(), serializer.errors
    return serializer


def test_a_patch_read_before_a_delete_cannot_bring_the_job_back(team_world: TeamWorld) -> None:
    job = _draft(team_world, created_by=team_world.admin)
    patch = _read_then_patch(job, team_world.admin, {"title": "Renamed"})
    delete_job(job, actor=team_world.admin)

    with pytest.raises(PermissionDenied) as refused:
        patch.save()

    assert refused.value.status_code == 403
    assert refused.value.detail == NOT_YOURS
    job.refresh_from_db()
    assert job.deleted_at is not None
    assert job.title != "Renamed"


def test_a_patch_read_before_a_close_keeps_the_job_closed(team_world: TeamWorld) -> None:
    job = team_world.job_b
    patch = _read_then_patch(job, team_world.admin, {"note_for_recruiters": "Call first"})
    close_job(job, actor=team_world.admin)

    patch.save()

    job.refresh_from_db()
    assert job.status == JobStatus.CLOSED
    assert job.closed_at is not None
    assert job.note_for_recruiters == "Call first"


def test_a_patch_read_before_the_admin_posted_is_refused_to_the_draft_only_ta(
    team_world: TeamWorld,
) -> None:
    job = _draft(team_world, status=JobStatus.SUBMITTED)
    patch = _read_then_patch(job, team_world.ta_draft, {"title": "Still mine"})
    post_job(job, actor=team_world.admin)

    with pytest.raises(PermissionDenied):
        patch.save()

    job.refresh_from_db()
    assert job.status == JobStatus.PENDING
    assert job.title != "Still mine"


def test_a_patch_read_on_a_private_job_is_checked_again_once_it_is_posted(
    team_world: TeamWorld,
) -> None:
    # Blank roles pass on a submitted job, not on a posted one.
    job = _draft(team_world, status=JobStatus.SUBMITTED)
    patch = _read_then_patch(job, team_world.admin, {"roles": ""})
    post_job(job, actor=team_world.admin)

    with pytest.raises(ValidationError) as refused:
        patch.save()

    assert refused.value.detail == {"roles": [REQUIRED_MESSAGES["roles"]]}
    job.refresh_from_db()
    assert job.status == JobStatus.PENDING
    assert job.roles


def test_a_patch_writes_only_the_fields_it_changes(team_world: TeamWorld) -> None:
    job = team_world.job_b
    patch = _read_then_patch(job, team_world.admin, {"title": "Growth Lead II"})
    Job.objects.filter(pk=job.pk).update(note_for_recruiters="Written meanwhile")

    patch.save()

    job.refresh_from_db()
    assert job.title == "Growth Lead II"
    assert job.note_for_recruiters == "Written meanwhile"


def test_a_patch_updates_only_the_changed_columns(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    job = team_world.job_b

    with CaptureQueriesContext(connection) as queries:
        response = client_for(team_world.admin).patch(
            f"/api/v1/jobs/{job.pk}", {"title": "Growth Lead II"}, format="json"
        )

    assert response.status_code == 200
    updates = [
        q["sql"] for q in queries.captured_queries if q["sql"].startswith('UPDATE "jobs_job"')
    ]
    assert len(updates) == 1
    assert '"title"' in updates[0]
    assert '"updated_at"' in updates[0]
    for column in ('"status"', '"deleted_at"', '"closed_at"', '"published_at"', '"team_id"'):
        assert column not in updates[0]


def test_patch_with_intent_post_by_ta_without_publish_rolls_back_the_edit(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    job = _draft(team_world, title="Original")

    response = client_for(team_world.ta_draft).patch(
        f"/api/v1/jobs/{job.pk}", {"title": "Changed", "intent": "post"}, format="json"
    )

    assert response.status_code == 403
    job.refresh_from_db()
    assert (job.title, job.status) == ("Original", JobStatus.DRAFT)


# --- R22: an admin moving a job to another team ----------------------------------------------


def test_admin_cannot_move_a_members_draft_either(team_world: TeamWorld) -> None:
    # The API never shows an admin a member's draft; the rule holds for it all the same.
    request = APIRequestFactory().patch("/")
    request.user = team_world.admin
    serializer = JobSerializer(
        _draft(team_world),
        data={"team": str(team_world.team_b.pk)},
        partial=True,
        context={"request": request},
    )

    assert not serializer.is_valid()
    assert serializer.errors == {"team": [PRIVATE_JOB_TEAM_MESSAGE]}


def test_admin_moving_a_submitted_job_of_a_member_is_400(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    job = _draft(team_world, status=JobStatus.SUBMITTED)

    response = client_for(team_world.admin).patch(
        f"/api/v1/jobs/{job.pk}", {"team": str(team_world.team_b.pk)}, format="json"
    )

    assert response.status_code == 400
    assert response.data == {"team": [PRIVATE_JOB_TEAM_MESSAGE]}
    job.refresh_from_db()
    assert job.team_id == team_world.team_a.pk


def test_admin_sending_the_same_team_on_a_members_submitted_job_is_fine(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    job = _draft(team_world, status=JobStatus.SUBMITTED)

    response = client_for(team_world.admin).patch(
        f"/api/v1/jobs/{job.pk}",
        {"team": str(team_world.team_a.pk), "title": "Renamed"},
        format="json",
    )

    assert response.status_code == 200
    job.refresh_from_db()
    assert (job.team_id, job.title) == (team_world.team_a.pk, "Renamed")


@pytest.mark.parametrize("status", [JobStatus.DRAFT, JobStatus.SUBMITTED])
def test_admin_may_move_their_own_private_job(
    team_world: TeamWorld, client_for: ClientFor, status: str
) -> None:
    job = _draft(team_world, status=status, created_by=team_world.admin)

    response = client_for(team_world.admin).patch(
        f"/api/v1/jobs/{job.pk}", {"team": str(team_world.team_b.pk)}, format="json"
    )

    assert response.status_code == 200
    job.refresh_from_db()
    assert job.team_id == team_world.team_b.pk


def test_moving_a_posted_job_withdraws_its_pending_proposals(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    job = _draft(team_world, status=JobStatus.ACTIVE)
    elsewhere = _draft(team_world, status=JobStatus.ACTIVE)
    proposal = propose_change(
        job, actor=team_world.ta_draft, kind=ChangeKind.EDIT, changes={"title": "Staff"}
    )
    other = propose_change(
        elsewhere, actor=team_world.ta_draft, kind=ChangeKind.EDIT, changes={"title": "Staff"}
    )
    client = client_for(team_world.admin)

    kept = client.patch(
        f"/api/v1/jobs/{job.pk}",
        {"team": str(team_world.team_a.pk), "note_for_recruiters": "x"},
        format="json",
    )
    proposal.refresh_from_db()
    assert kept.status_code == 200
    assert proposal.status == ChangeStatus.PENDING

    moved = client.patch(
        f"/api/v1/jobs/{job.pk}", {"team": str(team_world.team_b.pk)}, format="json"
    )

    assert moved.status_code == 200
    proposal.refresh_from_db()
    other.refresh_from_db()
    assert proposal.status == ChangeStatus.WITHDRAWN
    assert other.status == ChangeStatus.PENDING
