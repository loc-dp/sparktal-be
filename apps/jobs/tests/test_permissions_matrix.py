"""Spec §4: what each member may do to a job, one parametrized row per matrix cell."""

from dataclasses import asdict
from typing import cast

import pytest

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory
from apps.jobs.access import (
    JobPermissions,
    can_change_job,
    can_create_jobs,
    can_view_job,
    has_live_applications,
    job_permissions,
    jobs_visible_to,
)
from apps.jobs.models import Job, JobStatus
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db


def actor_of(world: TeamWorld, actor: str) -> User:
    return {
        "admin": world.admin,
        "ta_publish": world.ta_publish,
        "ta_draft": world.ta_draft,
        "hm": world.hm_a,
    }[actor]


def granted(permissions: JobPermissions) -> set[str]:
    return {name for name, on in asdict(permissions).items() if on}


def team_a_job(world: TeamWorld, status: str, created_by: User) -> Job:
    return cast(
        Job,
        JobFactory(company=world.company, team=world.team_a, created_by=created_by, status=status),
    )


@pytest.mark.parametrize(
    ("actor", "status", "own", "expected"),
    [
        ("admin", "draft", True, {"edit", "post", "delete", "review_proposals"}),
        ("admin", "submitted", False, {"edit", "post", "review_proposals"}),
        ("admin", "active", False, {"edit", "close", "review_proposals"}),
        ("admin", "closed", False, {"edit", "reopen", "delete", "review_proposals"}),
        ("ta_publish", "draft", True, {"edit", "post", "delete"}),
        # Ruling R7: sent for approval before posting rights were granted.
        ("ta_publish", "submitted", True, {"edit", "post"}),
        ("ta_publish", "active", True, {"edit", "close"}),
        ("ta_publish", "active", False, set()),
        ("ta_draft", "draft", True, {"edit", "submit", "delete"}),
        ("ta_draft", "submitted", True, {"edit", "withdraw"}),
        ("ta_draft", "active", True, {"propose"}),
        ("ta_draft", "closed", True, {"propose"}),
        ("hm", "active", False, set()),
    ],
)
def test_job_permissions(
    actor: str, status: str, own: bool, expected: set[str], team_world: TeamWorld
) -> None:
    user = actor_of(team_world, actor)
    # Someone else's job is a teammate's; the admin's "someone else" is ta_draft.
    other = team_world.ta_draft if actor in ("admin", "ta_publish") else team_world.ta_publish
    job = team_a_job(team_world, status, user if own else other)

    permissions = job_permissions(user, job)

    assert granted(permissions) == expected
    assert can_change_job(user, job) is permissions.edit


def test_delete_needs_no_live_applications(team_world: TeamWorld) -> None:
    job = team_a_job(team_world, JobStatus.CLOSED, team_world.ta_publish)
    application = cast(Application, ApplicationFactory(job=job, stage=Stage.SCREEN))

    assert has_live_applications(job)
    assert not job_permissions(team_world.admin, job).delete
    assert not job_permissions(team_world.ta_publish, job).delete

    Application.objects.filter(pk=application.pk).update(stage=Stage.REJECTED)
    ApplicationFactory(job=job, stage=Stage.ONBOARDED)

    assert not has_live_applications(job)
    assert job_permissions(team_world.admin, job).delete
    assert job_permissions(team_world.ta_publish, job).delete


def test_a_job_the_user_cannot_see_grants_nothing(team_world: TeamWorld) -> None:
    assert job_permissions(team_world.hm_b, team_world.job_a) == JobPermissions.NONE
    assert job_permissions(None, team_world.job_a) == JobPermissions.NONE
    assert granted(JobPermissions.NONE) == set()


def test_ta_with_publish_cannot_post_a_teammates_draft(team_world: TeamWorld) -> None:
    submitted = team_a_job(team_world, JobStatus.SUBMITTED, team_world.ta_draft)

    assert not can_view_job(team_world.ta_publish, submitted)
    assert granted(job_permissions(team_world.ta_publish, submitted)) == set()


def test_unsent_drafts_are_private_to_their_creator(team_world: TeamWorld) -> None:
    draft = team_a_job(team_world, JobStatus.DRAFT, team_world.ta_draft)
    submitted = team_a_job(team_world, JobStatus.SUBMITTED, team_world.ta_draft)

    assert can_view_job(team_world.ta_draft, draft)
    assert can_view_job(team_world.ta_draft, submitted)
    for teammate in (team_world.ta_publish, team_world.hm_a):
        assert not can_view_job(teammate, draft)
        assert not can_view_job(teammate, submitted)
        assert draft not in jobs_visible_to(teammate)
        assert submitted not in jobs_visible_to(teammate)
    # The admin sees a draft once it is sent to them, never before.
    assert not can_view_job(team_world.admin, draft)
    assert can_view_job(team_world.admin, submitted)
    assert set(jobs_visible_to(team_world.admin)) == {
        team_world.job_a,
        team_world.job_b,
        submitted,
    }


def test_only_admins_and_talent_acquisition_create_jobs(team_world: TeamWorld) -> None:
    assert can_create_jobs(team_world.admin)
    assert can_create_jobs(team_world.ta_publish)
    assert can_create_jobs(team_world.ta_draft)
    assert not can_create_jobs(team_world.hm_a)
    assert not can_create_jobs(None)
