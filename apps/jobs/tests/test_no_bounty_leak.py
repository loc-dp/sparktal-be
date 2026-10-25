"""Spec §3 anti-goal: no company-facing API shows the bounty. Hiding it in the FE is not enough.

Every GET a company user's screens call is walked for any key with "bounty" in it, and the
OpenAPI contract is checked the same way. A company that sends bounty fields anyway (an old
tab, an edit proposal) has them dropped without an error.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory
from apps.hiring.tests.factories import PlacementFactory
from apps.jobs.models import ChangeKind, Job, JobStatus
from apps.jobs.proposals import propose_change
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobFactory, JobQuestionFactory

pytestmark = pytest.mark.django_db

ClientFor = Callable[[User], APIClient]

COMPANY_GETS = [
    "/api/v1/jobs",
    "/api/v1/jobs/{job}",
    "/api/v1/jobs/{job}/overview",
    "/api/v1/jobs/{job}/questions",
    "/api/v1/jobs/counts",
    "/api/v1/company/home",
    "/api/v1/public/jobs/{token}",
    "/api/v1/jobs/{job}/change-requests",
    "/api/v1/company/change-requests",
    "/api/v1/jobs/{job}/applications",
    "/api/v1/applications/{application}",
    "/api/v1/company/placements",
    "/api/v1/company/talent-pool",
    "/api/v1/chat/channels",
]

# The company-facing payloads, under the names drf-spectacular gives them.
COMPANY_COMPONENTS = [
    "Job",
    "JobRequest",
    "PatchedJobRequest",
    "JobListItem",
    "JobOverview",
    "CompanyHome",
    "ChangeRequest",
    "PublicJob",
    "CompanyQuestion",
    "AnswerCreateRequest",
    "CompanyPlacement",
    "ApplicationDetail",
    "PaginatedCompanyPlacementList",
    "PlacementJob",
    "PlacementStatusEnum",
    "PlacementOutcomeEnum",
    "PlacementReportRequest",
    "PatchedPlacementStartDateRequest",
    "HireRequest",
]


def bounty_keys(node: Any, path: str = "$") -> list[str]:
    """Where in `node` a dict key mentions the bounty."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}"
            if "bounty" in str(key).lower():
                found.append(here)
            found += bounty_keys(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += bounty_keys(value, f"{path}[{index}]")
    return found


@pytest.fixture
def posted(team_world: TeamWorld) -> Job:
    """A live job the draft-only TA sent, with its bounty set."""
    job = cast(
        Job,
        JobFactory(
            company=team_world.company,
            team=team_world.team_a,
            created_by=team_world.ta_draft,
            status=JobStatus.ACTIVE,
            role_type="new_position",
            working_hours="40h_mon_fri",
            languages=["en"],
        ),
    )
    assert (job.bounty_type, job.bounty_pct) == ("pct", 18)
    return job


@pytest.fixture
def seeded(team_world: TeamWorld, posted: Job) -> dict[str, str]:
    """The job has its bounty set, one application, one proposal and one question."""
    application = cast(Application, ApplicationFactory(job=posted, stage=Stage.ONBOARDED))
    PlacementFactory(application=application)
    JobQuestionFactory(job=posted)
    propose_change(
        posted, actor=team_world.ta_draft, kind=ChangeKind.EDIT, changes={"title": "Payments Lead"}
    )
    return {
        "job": str(posted.pk),
        "token": posted.share_token,
        "application": str(application.pk),
    }


@pytest.mark.parametrize("url", COMPANY_GETS)
def test_company_endpoint_has_no_bounty_key(
    url: str, team_world: TeamWorld, seeded: dict[str, str], client_for: ClientFor
) -> None:
    response = client_for(team_world.admin).get(url.format(**seeded))

    assert response.status_code == 200, url
    assert bounty_keys(response.json()) == []


PAYOUT_WORDS = ("bounty", "payout", "paid")


def payout_keys(node: Any, path: str = "$") -> list[str]:
    """Where in `node` a dict key mentions the bounty, a payout or a payment."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}"
            if any(word in str(key).lower() for word in PAYOUT_WORDS):
                found.append(here)
            found += payout_keys(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += payout_keys(value, f"{path}[{index}]")
    return found


@pytest.mark.parametrize(
    "url",
    ["/api/v1/company/placements", "/api/v1/applications/{application}", "/api/v1/jobs/{job}"],
)
def test_company_payloads_have_no_payout_fields(
    url: str, team_world: TeamWorld, seeded: dict[str, str], client_for: ClientFor
) -> None:
    response = client_for(team_world.admin).get(url.format(**seeded))

    assert response.status_code == 200, url
    body = response.json()
    assert payout_keys(body) == []
    if "placements" in url:
        assert body["count"] == 1  # the walk saw a placement, not an empty page
    if "applications" in url:
        assert body["placement"] is not None


def test_the_payout_walk_finds_what_it_should() -> None:
    leaked = {"results": [{"payout_status": "due", "x": {"paid_amount": 1}}], "Bounty": 1}

    assert payout_keys(leaked) == [
        "$.results[0].payout_status",
        "$.results[0].x.paid_amount",
        "$.Bounty",
    ]


def test_the_walk_finds_a_bounty_key() -> None:
    """The control: the walker would catch a leak nested in lists and dicts."""
    leaked = {"results": [{"job": {"bounty_label": "18%"}}], "Bounty": 1}

    assert bounty_keys(leaked) == ["$.results[0].job.bounty_label", "$.Bounty"]


def test_openapi_company_components_have_no_bounty() -> None:
    schema = yaml.safe_load((Path(__file__).parents[3] / "openapi.yaml").read_text())
    components = schema["components"]["schemas"]

    for name in COMPANY_COMPONENTS:
        assert name in components, name
        assert bounty_keys(components[name]) == [], name


def test_company_bounty_input_is_ignored(
    team_world: TeamWorld, posted: Job, client_for: ClientFor
) -> None:
    response = client_for(team_world.admin).patch(
        f"/api/v1/jobs/{posted.pk}",
        {"title": "Payments Staff Engineer", "bounty_pct": 25, "bounty_type": "fixed"},
        format="json",
    )

    assert response.status_code == 200
    assert bounty_keys(response.json()) == []
    posted.refresh_from_db()
    assert posted.title == "Payments Staff Engineer"
    assert (posted.bounty_type, posted.bounty_pct, posted.bounty_amount) == ("pct", 18, None)


def test_company_bounty_input_alone_changes_nothing(
    team_world: TeamWorld, posted: Job, client_for: ClientFor
) -> None:
    response = client_for(team_world.admin).patch(
        f"/api/v1/jobs/{posted.pk}", {"bounty_pct": 25}, format="json"
    )

    assert response.status_code == 200
    posted.refresh_from_db()
    assert posted.bounty_pct == 18


def test_new_job_has_no_bounty(team_world: TeamWorld, client_for: ClientFor) -> None:
    response = client_for(team_world.admin).post(
        "/api/v1/jobs",
        {
            "title": "Risk Analyst",
            "team": str(team_world.team_a.pk),
            "bounty_type": "fixed",
            "bounty_pct": 25,
            "bounty_amount": 90_000_000,
        },
        format="json",
    )

    assert response.status_code == 201
    assert bounty_keys(response.json()) == []
    job = Job.objects.get(pk=response.json()["id"])
    assert (job.bounty_type, job.bounty_pct, job.bounty_amount) == (None, None, None)


def test_proposal_drops_bounty_keys(team_world: TeamWorld, posted: Job) -> None:
    request = propose_change(
        posted,
        actor=team_world.ta_draft,
        kind=ChangeKind.EDIT,
        changes={"title": "x", "bounty_pct": 25},
    )

    assert request.changes == {"title": "x"}
