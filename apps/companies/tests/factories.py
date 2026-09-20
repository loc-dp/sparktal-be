from datetime import UTC

import factory
from factory.django import DjangoModelFactory

from apps.accounts.tests.factories import UserFactory
from apps.companies.models import (
    Company,
    CompanyMembership,
    CompanyRole,
    CompanyType,
    Industry,
    Location,
    Office,
    SizeRange,
)


class CompanyFactory(DjangoModelFactory[Company]):
    class Meta:
        model = Company

    name = factory.Sequence(lambda n: f"Company {n}")
    industry = Industry.FINTECH
    company_type = CompanyType.FDI
    year_incorporated = 2019
    size_range = SizeRange.R100_500
    bd_headcount = 12
    located = Location.HCM
    about = factory.Faker("paragraph", nb_sentences=8)
    profile_completed_at = factory.Faker("date_time_this_year", tzinfo=UTC)


class OfficeFactory(DjangoModelFactory[Office]):
    class Meta:
        model = Office

    company = factory.SubFactory(CompanyFactory)
    address = factory.Sequence(lambda n: f"{n} Lê Duẩn, District 1, Ho Chi Minh City")
    city = Location.HCM
    position = factory.Sequence(lambda n: n)


class CompanyMembershipFactory(DjangoModelFactory[CompanyMembership]):
    class Meta:
        model = CompanyMembership

    user = factory.SubFactory(UserFactory)
    company = factory.SubFactory(CompanyFactory)
    role = CompanyRole.ADMIN
