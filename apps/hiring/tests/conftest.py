from collections.abc import Callable
from typing import cast

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.companies.tests.factories import TeamFactory
from apps.jobs.models import Job
from apps.jobs.tests.conftest import team_world  # noqa: F401  (shared with the placement tests)
from apps.recruiters.models import RecruiterProfile


def at_stage(application: Application, stage: str) -> Application:
    """Jump an application to `stage` without going through the workflow."""
    Application.objects.filter(pk=application.pk).update(stage=stage)
    application.refresh_from_db()
    return application


@pytest.fixture
def application(joined_job: Job, recruiter_profile: RecruiterProfile) -> Application:
    candidate = CandidateFactory(
        owner=recruiter_profile,
        full_name="Phạm Minh Quân",
        current_title="Backend Engineer · Tiki",
        location="hcm",
        languages=["Vietnamese", "English"],
        skills=["Go", "Kafka"],
        years_experience=6,
        current_salary=35_000_000,
        expected_salary=50_000_000,
    )
    return cast(
        Application,
        ApplicationFactory(
            job=joined_job,
            candidate=candidate,
            recruiter=recruiter_profile,
            stage=Stage.NEW,
            motivation="Wants to own a payments ledger.",
            consultant_note="Strong on Go and Kafka.",
        ),
    )


@pytest.fixture
def ta_client(company_ta: User, client_for: Callable[[User], APIClient]) -> APIClient:
    return client_for(company_ta)


@pytest.fixture
def admin_client(company_admin: User, client_for: Callable[[User], APIClient]) -> APIClient:
    return client_for(company_admin)


def move_to_another_team(member: User) -> None:
    """The member leaves the job's team, and with it every job of that team."""
    membership = member.membership
    membership.team = TeamFactory(company=membership.company)
    membership.save(update_fields=["team"])


@pytest.fixture
def team_hm(company_hm: User, job: Job) -> User:
    """A Hiring Manager in the job's team, so they see the job and its candidates."""
    membership = company_hm.membership
    membership.team = job.team
    membership.save(update_fields=["team"])
    return company_hm
