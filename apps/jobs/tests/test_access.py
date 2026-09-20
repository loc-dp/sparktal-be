import pytest

from apps.accounts.models import User
from apps.companies.models import Company
from apps.jobs.access import can_change_job, can_view_job, grant_creator_perms, jobs_visible_to
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db


def test_admin_and_ta_see_every_company_job(
    company: Company, company_admin: User, company_ta: User
) -> None:
    job = JobFactory(company=company)

    assert list(jobs_visible_to(company_admin)) == [job]
    assert list(jobs_visible_to(company_ta)) == [job]
    assert can_view_job(company_ta, job)
    assert can_change_job(company_ta, job)


def test_hiring_manager_only_sees_granted_jobs(company: Company, company_hm: User) -> None:
    mine = JobFactory(company=company, created_by=company_hm)
    grant_creator_perms(mine, company_hm)
    someone_elses = JobFactory(company=company)

    assert list(jobs_visible_to(company_hm)) == [mine]
    assert can_view_job(company_hm, mine)
    assert not can_view_job(company_hm, someone_elses)
    assert not can_change_job(company_hm, someone_elses)


def test_other_companies_and_recruiters_see_nothing(
    company: Company, other_company_admin: User, recruiter_user: User
) -> None:
    job = JobFactory(company=company)

    assert list(jobs_visible_to(other_company_admin)) == []
    assert list(jobs_visible_to(recruiter_user)) == []
    assert not can_view_job(other_company_admin, job)
    assert not can_view_job(recruiter_user, job)
