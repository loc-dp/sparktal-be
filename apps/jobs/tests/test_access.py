from typing import cast

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company
from apps.jobs.access import can_change_job, can_view_job, jobs_visible_to
from apps.jobs.models import Job
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db


def test_admin_sees_every_teams_jobs_and_a_member_only_their_own(team_world: TeamWorld) -> None:
    world = team_world

    assert set(jobs_visible_to(world.admin)) == {world.job_a, world.job_b}
    for member in (world.ta_publish, world.ta_draft, world.hm_a):
        assert list(jobs_visible_to(member)) == [world.job_a]
        assert can_view_job(member, world.job_a)
        assert not can_view_job(member, world.job_b)
    assert list(jobs_visible_to(world.hm_b)) == [world.job_b]
    assert not can_view_job(world.hm_b, world.job_a)


def test_a_hiring_manager_sees_their_teams_jobs_but_changes_none(team_world: TeamWorld) -> None:
    assert can_view_job(team_world.hm_a, team_world.job_a)
    assert not can_change_job(team_world.hm_a, team_world.job_a)
    assert can_change_job(team_world.admin, team_world.job_a)
    assert can_change_job(team_world.ta_publish, team_world.job_a)


def test_a_job_with_no_team_is_for_admins_only(team_world: TeamWorld) -> None:
    world = team_world
    teamless = cast(Job, JobFactory(company=world.company, team=None, created_by=world.admin))

    assert can_view_job(world.admin, teamless)
    for member in (world.ta_publish, world.ta_draft, world.hm_a, world.hm_b):
        assert not can_view_job(member, teamless)
        assert teamless not in jobs_visible_to(member)


def test_a_deleted_job_is_hidden_from_everyone(team_world: TeamWorld) -> None:
    world = team_world
    Job.objects.filter(pk=world.job_a.pk).update(deleted_at=timezone.now())
    world.job_a.refresh_from_db()

    for member in (world.admin, world.ta_publish, world.hm_a):
        assert not can_view_job(member, world.job_a)
        assert world.job_a not in jobs_visible_to(member)


def test_other_companies_and_recruiters_see_nothing(
    company: Company, other_company_admin: User, recruiter_user: User
) -> None:
    job = JobFactory(company=company)

    assert list(jobs_visible_to(other_company_admin)) == []
    assert list(jobs_visible_to(recruiter_user)) == []
    assert list(jobs_visible_to(None)) == []
    assert not can_view_job(other_company_admin, job)
    assert not can_view_job(recruiter_user, job)
    assert not can_view_job(None, job)
