from collections.abc import Callable
from typing import cast

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.chat.models import Channel
from apps.companies.models import Company, CompanyMembership, CompanyRole
from apps.companies.tests.factories import CompanyFactory, CompanyMembershipFactory, OfficeFactory
from apps.jobs.models import Job, JobAssignment
from apps.recruiters.models import RecruiterProfile, RecruiterStatus
from apps.recruiters.tests.factories import RecruiterProfileFactory


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Start every test with an empty cache so throttle counters never leak between tests."""
    cache.clear()


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def user(db: None) -> User:
    return cast(User, UserFactory())


@pytest.fixture
def auth_client(user: User) -> APIClient:
    client = APIClient()
    client.force_login(user)
    return client


@pytest.fixture
def client_for(db: None) -> Callable[[User], APIClient]:
    """Return a callable that logs `user` into a fresh APIClient."""

    def _login(member: User) -> APIClient:
        client = APIClient()
        client.force_login(member)
        return client

    return _login


@pytest.fixture
def company(db: None) -> Company:
    built = cast(Company, CompanyFactory(name="Vela Pay"))
    OfficeFactory(company=built, position=0)
    return built


def _member(target: Company, role: str, email: str) -> User:
    membership = cast(
        CompanyMembership, CompanyMembershipFactory(company=target, role=role, user__email=email)
    )
    return membership.user


@pytest.fixture
def company_admin(company: Company) -> User:
    return _member(company, CompanyRole.ADMIN, "admin@velapay.vn")


@pytest.fixture
def company_ta(company: Company) -> User:
    return _member(company, CompanyRole.TALENT_ACQUISITION, "ta@velapay.vn")


@pytest.fixture
def job(company: Company, company_ta: User) -> Job:
    from apps.jobs.tests.factories import JobFactory

    return cast(
        Job,
        JobFactory(company=company, created_by=company_ta, title="Senior Backend Engineer"),
    )


@pytest.fixture
def joined_job(job: Job, recruiter_profile: RecruiterProfile) -> Job:
    JobAssignment.objects.create(job=job, recruiter=recruiter_profile)
    return job


@pytest.fixture
def company_hm(company: Company) -> User:
    return _member(company, CompanyRole.HIRING_MANAGER, "hm@velapay.vn")


@pytest.fixture
def other_company_admin(db: None) -> User:
    other = cast(Company, CompanyFactory(name="Lumen Retail"))
    return _member(other, CompanyRole.ADMIN, "admin@lumen.vn")


@pytest.fixture
def recruiter_profile(db: None) -> RecruiterProfile:
    return cast(
        RecruiterProfile,
        RecruiterProfileFactory(nickname="NorthStar Talent", user__email="northstar@example.com"),
    )


@pytest.fixture
def recruiter_user(recruiter_profile: RecruiterProfile) -> User:
    return recruiter_profile.user


@pytest.fixture
def recruiter_client(recruiter_user: User) -> APIClient:
    client = APIClient()
    client.force_login(recruiter_user)
    return client


@pytest.fixture
def pending_recruiter_user(db: None) -> User:
    profile = cast(
        RecruiterProfile,
        RecruiterProfileFactory(
            nickname="Atlas Hunters",
            status=RecruiterStatus.PENDING,
            user__email="atlas@example.com",
        ),
    )
    return profile.user


@pytest.fixture
def rejected_recruiter_user(db: None) -> User:
    profile = cast(
        RecruiterProfile,
        RecruiterProfileFactory(
            nickname="Fog Bank Search",
            status=RecruiterStatus.REJECTED,
            user__email="fogbank@example.com",
        ),
    )
    return profile.user


@pytest.fixture
def channel(job: Job, recruiter_profile: RecruiterProfile) -> Channel:
    return Channel.objects.create(job=job, recruiter=recruiter_profile)


@pytest.fixture
def assignment(job: Job, recruiter_profile: RecruiterProfile) -> JobAssignment:
    return JobAssignment.objects.create(job=job, recruiter=recruiter_profile)
