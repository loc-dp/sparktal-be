"""Spec §3 anti-goal: a member never reaches another team's jobs, candidates or members.

Every surface the spec names (list, detail, counts, home, pipeline, applicants, candidate
detail, talent pool, chat, export, approval) is called at its real path. Outside what the
member may see, the answer is 404 or the row is simply absent.
"""

import json
from collections.abc import Callable
from datetime import timedelta
from typing import cast

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory
from apps.chat.models import Channel, Message
from apps.chat.tests.factories import MessageFactory
from apps.core.testing.pdf import make_pdf
from apps.exports.models import CvExport
from apps.hiring.models import ApprovalRequest, CandidateNote, Interview, InterviewFormat
from apps.jobs.access import can_view_job, jobs_visible_to
from apps.jobs.models import Job, JobStatus
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db

ClientFor = Callable[[User], APIClient]

# Each spec §3 surface at its real path (Ruling R3). "{job}" etc. are Team A objects.
JOB_SURFACES = [
    # job detail and the actions on it
    ("get", "/api/v1/jobs/{job}"),
    ("patch", "/api/v1/jobs/{job}"),
    ("get", "/api/v1/jobs/{job}/overview"),
    ("post", "/api/v1/jobs/{job}/close"),
    ("post", "/api/v1/jobs/{job}/reopen"),
    # applicants, and the pipeline board (the same list, filtered by stage)
    ("get", "/api/v1/jobs/{job}/applications"),
    ("get", "/api/v1/jobs/{job}/applications?stage=new"),
    # candidate detail
    ("get", "/api/v1/applications/{application}"),
    ("get", "/api/v1/applications/{application}/cv"),
    ("get", "/api/v1/applications/{application}/cv/inline"),
    ("get", "/api/v1/applications/{application}/cv/text"),
    ("get", "/api/v1/applications/{application}/notes"),
    ("post", "/api/v1/applications/{application}/notes"),
    ("delete", "/api/v1/notes/{note}"),
    ("get", "/api/v1/applications/{application}/feedback"),
    ("put", "/api/v1/interviews/{interview}/feedback"),
    ("get", "/api/v1/applications/{application}/messages"),
    ("post", "/api/v1/applications/{application}/messages"),
    # pipeline moves (the plan's "POST /applications/{id}/stage" is these commands)
    ("post", "/api/v1/applications/{application}/move-forward"),
    ("post", "/api/v1/applications/{application}/interviews"),
    ("post", "/api/v1/applications/{application}/offer"),
    ("post", "/api/v1/applications/{application}/hire"),
    ("post", "/api/v1/applications/{application}/archive"),
    # approval: the approvers list, asking, deciding
    ("get", "/api/v1/applications/{application}/approvers"),
    ("post", "/api/v1/applications/{application}/approval-requests"),
    ("post", "/api/v1/approval-requests/{approval}/decision"),
    # CV export
    ("post", "/api/v1/jobs/{job}/cv-exports"),
    ("get", "/api/v1/cv-exports/{export}"),
    ("get", "/api/v1/cv-exports/{export}/download"),
    # chat
    ("get", "/api/v1/chat/channels/{channel}/messages"),
    ("post", "/api/v1/chat/channels/{channel}/messages"),
    ("post", "/api/v1/chat/channels/{channel}/read"),
    ("get", "/api/v1/chat/messages/{attachment}/attachment"),
]
MEMBER_SURFACES = [
    ("patch", "/api/v1/company/members/{member}"),
    ("delete", "/api/v1/company/members/{member}"),
    ("post", "/api/v1/company/members/{member}/resend-invite"),
]
LISTS = [
    "/api/v1/jobs",
    "/api/v1/jobs/counts",
    "/api/v1/company/home",
    "/api/v1/company/talent-pool",
    "/api/v1/chat/channels",
    "/api/v1/approval-requests/waiting",
    "/api/v1/company/members",
    "/api/v1/company/teams",
]


def call(client: APIClient, method: str, url: str) -> Response:
    if method == "get":
        return cast(Response, client.get(url))
    return cast(Response, getattr(client, method)(url, {}, format="json"))


def channel_of(application: Application) -> Channel:
    return Channel.objects.get(job=application.job, recruiter=application.recruiter)


@pytest.fixture
def team_a(team_world: TeamWorld) -> dict[str, str]:
    """Team A's hiring activity on application_a; the ids every surface is called with."""
    world = team_world
    application = world.application_a
    note = CandidateNote.objects.create(application=application, author=world.admin, body="Strong")
    starts = timezone.now() + timedelta(days=1)
    interview = Interview.objects.create(
        application=application,
        round=1,
        starts_at=starts,
        ends_at=starts + timedelta(hours=1),
        format=InterviewFormat.ONLINE,
        created_by=world.ta_publish,
    )
    # hm_b was asked while still in team A, then moved to team B.
    approval = ApprovalRequest.objects.create(
        application=application,
        requested_by=world.ta_publish,
        approver=world.hm_b,
        stage=application.stage,
    )
    export = CvExport.objects.create(
        job=world.job_a, requested_by=world.ta_publish, application_ids=[application.pk]
    )
    channel = channel_of(application)
    attachment = cast(
        Message,
        MessageFactory(
            channel=channel,
            author=world.recruiter.user,
            body="",
            attachment=ContentFile(make_pdf("CV"), name="CV_Tran_Quoc_Bao.pdf"),
            attachment_name="CV_Tran_Quoc_Bao.pdf",
            attachment_size=120,
        ),
    )
    return {
        "job": str(world.job_a.pk),
        "application": str(application.pk),
        "note": str(note.pk),
        "interview": str(interview.pk),
        "approval": str(approval.pk),
        "export": str(export.pk),
        "channel": str(channel.pk),
        "attachment": str(attachment.pk),
        "member": str(world.hm_a.membership.pk),
    }


@pytest.fixture
def application_b(team_world: TeamWorld) -> Application:
    return cast(
        Application,
        ApplicationFactory(job=team_world.job_b, candidate__full_name="Lý Mỹ Duyên"),
    )


@pytest.mark.parametrize(("method", "url"), JOB_SURFACES + MEMBER_SURFACES)
def test_other_team_gets_404(
    method: str, url: str, team_world: TeamWorld, team_a: dict[str, str], client_for: ClientFor
) -> None:
    response = call(client_for(team_world.hm_b), method, url.format(**team_a))

    assert response.status_code == 404


@pytest.mark.parametrize(("method", "url"), JOB_SURFACES + MEMBER_SURFACES)
def test_own_team_reaches_the_same_surfaces(
    method: str, url: str, team_world: TeamWorld, team_a: dict[str, str], client_for: ClientFor
) -> None:
    """The control for the test above: the path is real and team A gets past the lookup."""
    response = call(client_for(team_world.hm_a), method, url.format(**team_a))

    assert response.status_code != 404


def _seen_by(client: APIClient, markers: list[str]) -> dict[str, list[str]]:
    leaks: dict[str, list[str]] = {}
    for url in LISTS:
        response = client.get(url)
        assert response.status_code == 200, url
        text = json.dumps(response.json(), ensure_ascii=False)
        found = [marker for marker in markers if marker in text]
        if found:
            leaks[url] = found
    return leaks


def test_lists_exclude_other_team(
    team_world: TeamWorld,
    team_a: dict[str, str],
    application_b: Application,
    client_for: ClientFor,
) -> None:
    world = team_world
    team_a_markers = [
        *team_a.values(),
        world.job_a.title,
        world.team_a.name,
        world.application_a.candidate.full_name,
        world.hm_a.email,
        world.ta_publish.email,
        world.ta_draft.email,
    ]
    team_b_markers = [
        str(world.job_b.pk),
        world.job_b.title,
        world.team_b.name,
        str(application_b.pk),
        application_b.candidate.full_name,
        str(channel_of(application_b).pk),
        world.hm_b.email,
    ]

    assert _seen_by(client_for(world.hm_b), team_a_markers) == {}
    assert _seen_by(client_for(world.ta_publish), team_b_markers) == {}
    assert _seen_by(client_for(world.hm_a), team_b_markers) == {}

    hm_b = client_for(world.hm_b)
    assert [row["id"] for row in hm_b.get("/api/v1/jobs").json()["results"]] == [
        str(world.job_b.pk)
    ]
    assert hm_b.get("/api/v1/jobs/counts").json() == {
        "draft": 0,
        "submitted": 0,
        "pending": 0,
        "active": 1,
        "closed": 0,
    }
    home = hm_b.get("/api/v1/company/home").json()
    assert home["jobs"]["active"] == 1
    assert home["pipeline"]["new"] == 1
    assert [row["id"] for row in home["active_jobs"]] == [str(world.job_b.pk)]
    assert hm_b.get("/api/v1/company/talent-pool").json()["count"] == 1
    assert [row["id"] for row in hm_b.get("/api/v1/chat/channels").json()] == [
        str(channel_of(application_b).pk)
    ]
    assert hm_b.get("/api/v1/approval-requests/waiting").json() == []


def test_teamless_job_is_admin_only(team_world: TeamWorld, client_for: ClientFor) -> None:
    """A job an admin opened before the company had teams belongs to no team."""
    world = team_world
    teamless = cast(
        Job,
        JobFactory(
            company=world.company, team=None, created_by=world.admin, title="Office Manager"
        ),
    )
    application = cast(Application, ApplicationFactory(job=teamless))

    admin = client_for(world.admin)
    assert admin.get(f"/api/v1/jobs/{teamless.pk}").status_code == 200
    assert str(teamless.pk) in {row["id"] for row in admin.get("/api/v1/jobs").json()["results"]}
    assert admin.get(f"/api/v1/applications/{application.pk}").status_code == 200

    for member in (world.ta_publish, world.ta_draft, world.hm_a, world.hm_b):
        client = client_for(member)
        assert client.get(f"/api/v1/jobs/{teamless.pk}").status_code == 404
        listed = {row["id"] for row in client.get("/api/v1/jobs").json()["results"]}
        assert str(teamless.pk) not in listed
        assert client.get(f"/api/v1/applications/{application.pk}").status_code == 404
        assert client.get(f"/api/v1/jobs/{teamless.pk}/applications").status_code == 404


def test_a_member_without_a_team_never_matches_a_teamless_job(team_world: TeamWorld) -> None:
    """Locks the no-team guard: without it, `team_id=None` would match every teamless job.

    The database forbids an HM without a team, so the membership is changed in memory only.
    """
    world = team_world
    teamless = cast(Job, JobFactory(company=world.company, team=None, created_by=world.admin))
    hm = world.hm_a
    hm.membership.team = None
    assert hm.membership.team_id is None

    assert not can_view_job(hm, teamless)
    assert teamless not in jobs_visible_to(hm)
    assert list(jobs_visible_to(hm)) == []


@pytest.mark.parametrize(("method", "url"), JOB_SURFACES)
def test_soft_deleted_job_is_404_everywhere(
    method: str, url: str, team_world: TeamWorld, team_a: dict[str, str], client_for: ClientFor
) -> None:
    Job.objects.filter(pk=team_world.job_a.pk).update(deleted_at=timezone.now())

    for member in (team_world.admin, team_world.ta_publish):
        response = call(client_for(member), method, url.format(**team_a))
        assert response.status_code == 404, member.email


def test_soft_deleted_job_is_absent_from_every_list(
    team_world: TeamWorld, team_a: dict[str, str], client_for: ClientFor
) -> None:
    world = team_world
    Job.objects.filter(pk=world.job_a.pk).update(deleted_at=timezone.now())
    markers = [
        team_a["job"],
        team_a["application"],
        team_a["channel"],
        team_a["approval"],
        world.job_a.title,
        world.application_a.candidate.full_name,
    ]

    for member in (world.admin, world.ta_publish, world.hm_a, world.hm_b):
        assert _seen_by(client_for(member), markers) == {}, member.email
    counts = client_for(world.admin).get("/api/v1/jobs/counts").json()
    assert counts == {
        "draft": 0,
        "submitted": 0,
        "pending": 0,
        "active": 1,
        "closed": 0,
    }


def test_approvers_list_excludes_other_team(team_world: TeamWorld, client_for: ClientFor) -> None:
    world = team_world
    client = client_for(world.ta_publish)
    url = f"/api/v1/applications/{world.application_a.pk}"

    approvers = {row["id"] for row in client.get(f"{url}/approvers").json()}

    assert approvers == {str(world.admin.pk), str(world.ta_draft.pk), str(world.hm_a.pk)}
    asked = client.post(
        f"{url}/approval-requests", {"approver_id": str(world.hm_b.pk), "note": ""}, format="json"
    )
    assert asked.status_code == 400
    assert "approver_id" in asked.json()


def _duplicate_on_job_b(world: TeamWorld) -> Application:
    """The person behind application_a, sent again later to team B's job."""
    first = world.application_a
    return cast(
        Application,
        ApplicationFactory(
            job=world.job_b,
            candidate__full_name=first.candidate.full_name,
            is_duplicate=True,
            duplicate_of=first,
            submitted_at=first.submitted_at + timedelta(days=3),
        ),
    )


def _team_a_details(world: TeamWorld) -> list[str]:
    first = world.application_a
    return [str(world.job_a.pk), world.job_a.title, world.team_a.name, str(first.pk)]


def test_talent_pool_shows_only_own_team_applications_of_shared_person(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    world = team_world
    duplicate = _duplicate_on_job_b(world)

    body = client_for(world.hm_b).get("/api/v1/company/talent-pool").json()

    assert body["count"] == 1
    [entry] = body["results"]
    assert [row["id"] for row in entry["applications"]] == [str(duplicate.pk)]
    assert entry["applications"][0]["is_duplicate"] is True
    text = json.dumps(entry, ensure_ascii=False)
    assert [detail for detail in _team_a_details(world) if detail in text] == []

    admin_body = client_for(world.admin).get("/api/v1/company/talent-pool").json()
    assert admin_body["count"] == 1
    assert {row["id"] for row in admin_body["results"][0]["applications"]} == {
        str(duplicate.pk),
        str(world.application_a.pk),
    }


def test_first_submitted_at_hidden_when_first_application_is_other_team(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    world = team_world
    duplicate = _duplicate_on_job_b(world)
    url = f"/api/v1/applications/{duplicate.pk}"

    response = client_for(world.hm_b).get(url)

    assert response.status_code == 200
    body = response.json()
    assert body["is_duplicate"] is True
    assert body["first_submitted_at"] is None
    text = json.dumps(body, ensure_ascii=False)
    assert [detail for detail in _team_a_details(world) if detail in text] == []

    seen_by_admin = client_for(world.admin).get(url).json()["first_submitted_at"]
    assert parse_datetime(seen_by_admin) == world.application_a.submitted_at


def test_hm_still_moves_candidates_in_own_team(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    application = team_world.application_a

    response = client_for(team_world.hm_a).post(
        f"/api/v1/applications/{application.pk}/move-forward"
    )

    assert response.status_code == 200
    assert response.json()["stage"] == Stage.SCREEN


def test_hm_sees_own_team_job_but_cannot_change_it(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    client = client_for(team_world.hm_a)
    url = f"/api/v1/jobs/{team_world.job_a.pk}"

    assert client.get(url).status_code == 200
    response = client.patch(url, {"title": "Renamed"}, format="json")

    assert response.status_code == 403
    assert response.json()["detail"] == "You can't do that on this job."
    assert client.post(f"{url}/close").status_code == 403
    team_world.job_a.refresh_from_db()
    assert team_world.job_a.title == "Payments Backend Engineer"
    assert team_world.job_a.status == JobStatus.ACTIVE
