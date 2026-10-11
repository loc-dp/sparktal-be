from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, connection

from apps.candidates.tests.factories import CandidateFactory
from apps.companies.models import (
    Company,
    CompanyRole,
    Industry,
    Location,
    Office,
    SizeRange,
    location_codes,
)
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


def test_binh_duong_is_part_of_ho_chi_minh_city_since_the_2025_merger() -> None:
    assert "bd" not in Location.values
    codes = location_codes(with_other=False)
    assert "hcm = Ho Chi Minh City (incl. Bình Dương, Bà Rịa - Vũng Tàu)" in codes
    assert "other = " not in codes
    assert location_codes(with_other=True).endswith("other = Other province")


def test_rows_in_binh_duong_move_to_ho_chi_minh_city() -> None:
    office = OfficeFactory(city="bd")
    Company.objects.filter(pk=office.company_id).update(located="bd")
    candidate = CandidateFactory(location="bd")
    editor = SimpleNamespace(connection=connection)

    for migration in (
        "apps.companies.migrations.0002_binh_duong_into_hcm",
        "apps.candidates.migrations.0008_binh_duong_into_hcm",
    ):
        import_module(migration).binh_duong_into_hcm(django_apps, editor)

    office.refresh_from_db()
    candidate.refresh_from_db()
    assert (office.city, candidate.location) == ("hcm", "hcm")
    assert Company.objects.get(pk=office.company_id).located == "hcm"
