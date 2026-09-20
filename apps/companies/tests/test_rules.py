import pytest
from django.contrib.auth.models import AnonymousUser

from apps.accounts.models import User
from apps.companies.access import company_of, membership_of
from apps.companies.models import Company

pytestmark = pytest.mark.django_db


def test_admin_and_ta_can_change_the_company(
    company: Company, company_admin: User, company_ta: User
) -> None:
    assert company_admin.has_perm("companies.change_company", company)
    assert company_ta.has_perm("companies.change_company", company)


def test_hiring_manager_can_view_but_not_change(company: Company, company_hm: User) -> None:
    assert company_hm.has_perm("companies.view_company", company)
    assert not company_hm.has_perm("companies.change_company", company)


def test_other_companies_and_recruiters_have_no_access(
    company: Company, other_company_admin: User, recruiter_user: User
) -> None:
    assert not other_company_admin.has_perm("companies.view_company", company)
    assert not recruiter_user.has_perm("companies.view_company", company)
    assert not AnonymousUser().has_perm("companies.view_company", company)


def test_every_member_may_add_jobs(
    company_admin: User, company_ta: User, company_hm: User, recruiter_user: User
) -> None:
    assert company_admin.has_perm("jobs.add_job")
    assert company_ta.has_perm("jobs.add_job")
    assert company_hm.has_perm("jobs.add_job")
    assert not recruiter_user.has_perm("jobs.add_job")


def test_access_helpers(company: Company, company_ta: User, recruiter_user: User) -> None:
    assert company_of(company_ta) == company
    membership = membership_of(company_ta)
    assert membership is not None
    assert membership.role == "talent_acquisition"
    assert company_of(recruiter_user) is None
    assert membership_of(recruiter_user) is None
