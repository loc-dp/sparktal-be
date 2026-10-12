import pytest
from django.core.management import call_command

from apps.companies.models import Company, CompanyMembership, CompanyRole
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db


def test_seed_creates_vela_pay_with_members_and_offices() -> None:
    call_command("seed_demo", "--force")

    company = Company.objects.get(name="Vela Pay")
    assert company.profile_completed is True
    assert company.industry == "fintech"
    assert company.offices.count() >= 1
    roles = {m.user.email: m.role for m in company.memberships.all()}
    assert roles["nhung.le@velapay.vn"] == CompanyRole.ADMIN
    assert roles["maianh.tran@velapay.vn"] == CompanyRole.TALENT_ACQUISITION
    assert roles["khoa.nguyen@velapay.vn"] == CompanyRole.HIRING_MANAGER


def test_seed_creates_four_recruiters_with_nicknames() -> None:
    call_command("seed_demo", "--force")

    assert set(RecruiterProfile.objects.values_list("nickname", flat=True)) == {
        "NorthStar Talent",
        "Bluefin Search",
        "Atlas Hunters",
        "Quasar Sourcing",
    }


def test_seed_creates_an_onboarding_company_for_e2e() -> None:
    call_command("seed_demo", "--force")

    company = Company.objects.get(name="Kestrel Logistics")
    assert company.profile_completed is False
    assert company.memberships.get(user__email="new.admin@example.com").role == CompanyRole.ADMIN


def test_seed_resets_the_onboarding_company() -> None:
    call_command("seed_demo", "--force")
    company = Company.objects.get(name="Kestrel Logistics")
    company.offices.create(address="Left over from a previous run", city="hp", position=0)
    company.industry = "fintech"
    company.mark_profile_completed()

    call_command("seed_demo", "--force")

    company.refresh_from_db()
    assert company.offices.count() == 0
    assert company.industry == ""
    assert company.profile_completed is False


def test_seed_is_idempotent() -> None:
    call_command("seed_demo", "--force")
    call_command("seed_demo", "--force")

    assert Company.objects.filter(name="Vela Pay").count() == 1


def test_seed_has_an_invited_member_without_a_password() -> None:
    call_command("seed_demo", "--force")

    member = CompanyMembership.objects.get(user__email="tuan.vo@velapay.vn")
    assert member.company.name == "Vela Pay"
    assert not member.user.has_usable_password()


def test_reset_removes_teammates_left_behind_by_e2e() -> None:
    from apps.accounts.models import User
    from apps.companies.seed import reset

    call_command("seed_demo", "--force")
    leftover = User.objects.create_user(
        email="e2e.123@velapay.vn", password=None, full_name="E2E Teammate", kind="company"
    )
    CompanyMembership.objects.create(
        user=leftover,
        company=Company.objects.get(name="Vela Pay"),
        role=CompanyRole.TALENT_ACQUISITION,
    )

    reset()

    assert not User.objects.filter(email="e2e.123@velapay.vn").exists()
