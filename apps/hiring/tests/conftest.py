from collections.abc import Callable
from typing import cast

import pytest
from guardian.shortcuts import assign_perm
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.jobs.models import Job
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


@pytest.fixture
def granted_hm(company_hm: User, job: Job) -> User:
    assign_perm("jobs.view_job", company_hm, job)
    return company_hm
