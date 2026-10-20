from collections.abc import Callable
from typing import cast

import pytest
from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User, UserKind
from apps.accounts.tests.factories import UserFactory
from apps.companies.members import EMAIL_TAKEN, set_locked
from apps.companies.models import Company, CompanyMembership, CompanyRole
from apps.companies.provisioning import COMPANY_NAME_LENGTH, provision_company
from apps.companies.tests.factories import CompanyMembershipFactory

pytestmark = pytest.mark.django_db


def provision(**overrides: str) -> CompanyMembership:
    body = {
        "name": "Nordhaven",
        "admin_email": "admin@nordhaven.vn",
        "admin_full_name": "Phạm Quốc Anh",
    }
    return provision_company(**{**body, **overrides})


def test_provision_creates_the_company_and_its_first_admin() -> None:
    membership = provision(name="  Nordhaven  ", admin_email="Admin@NORDHAVEN.vn")

    assert membership.role == CompanyRole.ADMIN
    assert membership.team is None
    assert membership.can_publish is False
    company = membership.company
    assert company.name == "Nordhaven"
    assert company.profile_completed is False
    user = membership.user
    assert user.email == "admin@nordhaven.vn"
    assert user.full_name == "Phạm Quốc Anh"
    assert user.kind == UserKind.COMPANY
    assert user.is_active is True
    assert not user.has_usable_password()
    assert CompanyMembership.objects.get(user=user) == membership


@pytest.mark.parametrize(
    "taken",
    ["admin@nordhaven.vn", "ADMIN@nordhaven.vn", "Admin@Nordhaven.VN"],
    ids=["same", "upper-local-part", "mixed-case"],
)
def test_any_existing_user_makes_the_email_taken(taken: str) -> None:
    UserFactory(email="admin@nordhaven.vn", kind=UserKind.RECRUITER)

    with pytest.raises(ValidationError) as exc:
        provision(admin_email=taken)

    assert exc.value.detail == {"admin_email": [EMAIL_TAKEN]}
    assert not Company.objects.filter(name="Nordhaven").exists()


def test_an_inactive_user_still_holds_the_email() -> None:
    UserFactory(email="admin@nordhaven.vn", is_active=False)

    with pytest.raises(ValidationError) as exc:
        provision()

    assert exc.value.detail == {"admin_email": [EMAIL_TAKEN]}


@pytest.mark.parametrize("name", ["", "    ", "x" * 121])
def test_name_is_1_to_120_characters(name: str) -> None:
    with pytest.raises(ValidationError) as exc:
        provision(name=name)

    assert exc.value.detail == {"name": [COMPANY_NAME_LENGTH]}
    assert not User.objects.filter(email="admin@nordhaven.vn").exists()


def test_a_failure_after_provisioning_rolls_everything_back() -> None:
    """Callers send the invite inside the same transaction, so a failure undoes the company."""

    class InviteFailed(Exception):
        pass

    def provision_then_fail() -> None:
        with transaction.atomic():
            provision()
            raise InviteFailed

    with pytest.raises(InviteFailed):
        provision_then_fail()

    assert not Company.objects.filter(name="Nordhaven").exists()
    assert not User.objects.filter(email="admin@nordhaven.vn").exists()


# set_locked ----------------------------------------------------------------------------------


def test_lock_and_unlock_flip_only_the_account(company: Company, company_ta: User) -> None:
    membership = company_ta.membership
    team_id = membership.team_id

    locked = set_locked(membership, locked=True)

    company_ta.refresh_from_db()
    assert company_ta.is_active is False
    assert locked.user.is_active is False
    membership.refresh_from_db()
    assert (membership.company_id, membership.team_id) == (company.pk, team_id)
    assert membership.role == CompanyRole.TALENT_ACQUISITION
    assert company_ta.has_usable_password()

    set_locked(membership, locked=False)

    company_ta.refresh_from_db()
    assert company_ta.is_active is True


def test_the_only_admin_can_be_locked(company: Company) -> None:
    admin = cast(CompanyMembership, CompanyMembershipFactory(company=company))

    set_locked(admin, locked=True)

    admin.user.refresh_from_db()
    assert admin.user.is_active is False
    assert CompanyMembership.objects.filter(company=company, role=CompanyRole.ADMIN).count() == 1


def test_lock_signs_out_every_session_of_that_user_only(
    company_ta: User, company_admin: User, client_for: Callable[[User], APIClient]
) -> None:
    phone, laptop = client_for(company_ta), client_for(company_ta)
    teammate = client_for(company_admin)

    set_locked(company_ta.membership, locked=True)
    set_locked(company_ta.membership, locked=False)

    assert phone.get("/api/v1/me").status_code == 403
    assert laptop.get("/api/v1/me").status_code == 403
    assert teammate.get("/api/v1/me").status_code == 200
