"""`team_world`: one company split into two teams, shared by the team and permission tests."""

from dataclasses import dataclass
from typing import cast

import pytest

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory
from apps.companies.models import Company, CompanyMembership, CompanyRole, Team
from apps.companies.tests.factories import (
    CompanyFactory,
    CompanyMembershipFactory,
    OfficeFactory,
    TeamFactory,
)
from apps.jobs.models import Job, JobStatus
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.models import RecruiterProfile


@dataclass(frozen=True)
class TeamWorld:
    company: Company
    team_a: Team
    team_b: Team
    admin: User
    ta_publish: User  # Talent Acquisition in team A who may post
    ta_draft: User  # Talent Acquisition in team A who may only draft
    hm_a: User
    hm_b: User
    job_a: Job  # active, team A, created by ta_publish
    job_b: Job  # active, team B, created by admin
    application_a: Application  # on job_a, stage new
    recruiter: RecruiterProfile  # who submitted application_a


def _member(
    company: Company, role: str, email: str, team: Team | None = None, *, can_publish: bool = False
) -> User:
    membership = cast(
        CompanyMembership,
        CompanyMembershipFactory(
            company=company,
            role=role,
            team=team,
            can_publish=can_publish,
            user__email=email,
            user__full_name=email.split("@")[0].replace(".", " ").title(),
        ),
    )
    return membership.user


@pytest.fixture
def team_world(db: None) -> TeamWorld:
    company = cast(Company, CompanyFactory(name="Orbit Pay"))
    OfficeFactory(company=company, position=0)
    team_a = cast(Team, TeamFactory(company=company, name="Payments"))
    team_b = cast(Team, TeamFactory(company=company, name="Growth"))

    admin = _member(company, CompanyRole.ADMIN, "admin@orbit.vn")
    ta_publish = _member(
        company, CompanyRole.TALENT_ACQUISITION, "ta.publish@orbit.vn", team_a, can_publish=True
    )
    ta_draft = _member(company, CompanyRole.TALENT_ACQUISITION, "ta.draft@orbit.vn", team_a)
    hm_a = _member(company, CompanyRole.HIRING_MANAGER, "hm.a@orbit.vn", team_a)
    hm_b = _member(company, CompanyRole.HIRING_MANAGER, "hm.b@orbit.vn", team_b)

    job_a = cast(
        Job,
        JobFactory(
            company=company,
            team=team_a,
            created_by=ta_publish,
            title="Payments Backend Engineer",
            status=JobStatus.ACTIVE,
        ),
    )
    job_b = cast(
        Job,
        JobFactory(
            company=company,
            team=team_b,
            created_by=admin,
            title="Growth Marketing Lead",
            status=JobStatus.ACTIVE,
        ),
    )
    application_a = cast(
        Application,
        ApplicationFactory(job=job_a, stage=Stage.NEW, candidate__full_name="Trần Quốc Bảo"),
    )
    return TeamWorld(
        company=company,
        team_a=team_a,
        team_b=team_b,
        admin=admin,
        ta_publish=ta_publish,
        ta_draft=ta_draft,
        hm_a=hm_a,
        hm_b=hm_b,
        job_a=job_a,
        job_b=job_b,
        application_a=application_a,
        recruiter=application_a.recruiter,
    )
