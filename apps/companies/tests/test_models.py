import pytest
from django.db import IntegrityError

from apps.companies.models import Company, CompanyRole, Industry, Location, Office, SizeRange
from apps.companies.tests.factories import (
    CompanyFactory,
    CompanyMembershipFactory,
    OfficeFactory,
)

pytestmark = pytest.mark.django_db


def test_company_profile_is_incomplete_until_completed_at_is_set() -> None:
    company = CompanyFactory(profile_completed_at=None)

    assert company.profile_completed is False

    company.mark_profile_completed()

    assert company.profile_completed is True
    assert company.profile_completed_at is not None


def test_offices_are_ordered_by_position() -> None:
    company = CompanyFactory()
    OfficeFactory(company=company, position=1, address="2 Hai Bà Trưng, Q1, HCM")
    OfficeFactory(company=company, position=0, address="1 Lê Duẩn, Q1, HCM")

    assert [office.position for office in company.offices.all()] == [0, 1]
    assert company.offices.all()[0].address.startswith("1 Lê Duẩn")


def test_a_user_has_at_most_one_membership() -> None:
    membership = CompanyMembershipFactory(role=CompanyRole.TALENT_ACQUISITION)

    with pytest.raises(IntegrityError):
        CompanyMembershipFactory(user=membership.user, company=CompanyFactory())


def test_choice_sets_match_the_spec() -> None:
    assert len(Industry.choices) == 10
    assert Industry.FINTECH in Industry.values
    assert next(choice[1] for choice in SizeRange.choices) == "Under 50 employees"
    assert Location.HCM.label == "Ho Chi Minh City"
    assert [role[0] for role in CompanyRole.choices] == [
        "admin",
        "hiring_manager",
        "talent_acquisition",
    ]


def test_str_is_useful_in_admin() -> None:
    company = CompanyFactory(name="Vela Pay")

    assert str(company) == "Vela Pay"
    assert str(Office(company=company, address="1 Lê Duẩn")) == "Vela Pay — 1 Lê Duẩn"
    assert isinstance(Company.objects.get(pk=company.pk), Company)
