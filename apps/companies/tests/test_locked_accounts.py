"""A Sparktal lock holds: no company, and nothing short of Sparktal's unlock, can undo it."""

from collections.abc import Callable
from typing import Any, cast

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.members import ACCOUNT_LOCKED, EMAIL_TAKEN, remove_member, set_locked
from apps.companies.models import Company, CompanyMembership, CompanyRole
from apps.companies.tests.factories import CompanyFactory, CompanyMembershipFactory

pytestmark = pytest.mark.django_db

ClientFor = Callable[[User], APIClient]
MEMBERS = "/api/v1/company/members"


def reinvite(client: APIClient, user: User, **overrides: Any) -> Any:
    body = {"email": user.email, "full_name": "Back Again", "role": "admin", **overrides}
    return client.post(MEMBERS, body, format="json")


def locked_and_removed(company_ta: User, company_admin: User) -> User:
    set_locked(company_ta.membership, locked=True)
    remove_member(company_ta.membership, actor=company_admin)
    company_ta.refresh_from_db()
    return company_ta


# Remove + re-invite does not undo a lock ---------------------------------------------------------


def test_remove_and_reinvite_in_the_same_company_stays_locked(
    company: Company,
    company_admin: User,
    company_ta: User,
    client_for: ClientFor,
    mailoutbox: list[Any],
) -> None:
    team = company_ta.membership.team
    assert team is not None
    set_locked(company_ta.membership, locked=True)
    admin = client_for(company_admin)

    assert admin.delete(f"{MEMBERS}/{company_ta.membership.pk}").status_code == 204
    response = reinvite(admin, company_ta, role="talent_acquisition", team=str(team.pk))

    # The company side is told only that the address is taken: only Sparktal can unlock.
    assert response.status_code == 400
    assert response.json() == {"email": [EMAIL_TAKEN]}
    company_ta.refresh_from_db()
    assert company_ta.is_active is False
    assert company_ta.locked_at is not None
    assert not CompanyMembership.objects.filter(user=company_ta).exists()
    assert mailoutbox == []


def test_another_company_cannot_claim_a_locked_removed_user(
    company: Company, company_admin: User, company_ta: User, client_for: ClientFor
) -> None:
    user = locked_and_removed(company_ta, company_admin)
    other_admin = cast(
        CompanyMembership,
        CompanyMembershipFactory(company=CompanyFactory(name="Lumen"), role=CompanyRole.ADMIN),
    ).user

    response = reinvite(client_for(other_admin), user)

    # Not told of the lock: another company must not learn that Sparktal locked someone.
    assert response.status_code == 400
    assert response.json() == {"email": [EMAIL_TAKEN]}
    assert ACCOUNT_LOCKED not in response.content.decode()
    user.refresh_from_db()
    assert user.is_active is False
    assert user.locked_at is not None
    assert not CompanyMembership.objects.filter(user=user).exists()


def test_another_company_is_not_told_a_seated_member_is_locked(
    company: Company, company_ta: User, other_company_admin: User, client_for: ClientFor
) -> None:
    set_locked(company_ta.membership, locked=True)

    response = reinvite(client_for(other_company_admin), company_ta)

    assert response.status_code == 400
    assert response.json() == {"email": [EMAIL_TAKEN]}
    assert CompanyMembership.objects.get(user=company_ta).company_id == company.pk


def test_removing_a_locked_member_keeps_the_lock(
    company: Company, company_admin: User, company_ta: User
) -> None:
    set_locked(company_ta.membership, locked=True)
    company_ta.refresh_from_db()
    locked_at = company_ta.locked_at
    assert locked_at is not None

    remove_member(company_ta.membership, actor=company_admin)

    company_ta.refresh_from_db()
    assert company_ta.locked_at == locked_at
    assert company_ta.is_active is False


def test_a_removed_unlocked_member_can_still_be_invited_again(
    company: Company, company_admin: User, company_ta: User, client_for: ClientFor
) -> None:
    remove_member(company_ta.membership, actor=company_admin)

    response = reinvite(client_for(company_admin), company_ta)

    assert response.status_code == 201
    company_ta.refresh_from_db()
    assert company_ta.is_active is True
    assert company_ta.locked_at is None


# set_locked and the database --------------------------------------------------------------------


def test_lock_sets_locked_at_once_and_unlock_clears_it(company_ta: User) -> None:
    membership = company_ta.membership

    set_locked(membership, locked=True)
    company_ta.refresh_from_db()
    first = company_ta.locked_at
    assert first is not None
    set_locked(membership, locked=True)  # locking again keeps when it was first locked
    company_ta.refresh_from_db()
    assert company_ta.locked_at == first

    set_locked(membership, locked=False)

    company_ta.refresh_from_db()
    assert company_ta.locked_at is None
    assert company_ta.is_active is True


def test_the_database_refuses_to_switch_a_locked_user_back_on(company_ta: User) -> None:
    set_locked(company_ta.membership, locked=True)

    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=company_ta.pk).update(is_active=True)
    company_ta.refresh_from_db()
    company_ta.is_active = True
    with pytest.raises(IntegrityError), transaction.atomic():
        company_ta.save()

    assert User.objects.get(pk=company_ta.pk).is_active is False


# Status and resend read the lock, not is_active ------------------------------------------------


def test_member_status_reads_locked_at(
    company: Company, company_admin: User, company_ta: User, company_hm: User, client_for: ClientFor
) -> None:
    set_locked(company_ta.membership, locked=True)
    # Switched off some other way (not a Sparktal lock): not shown as locked.
    User.objects.filter(pk=company_hm.pk).update(is_active=False)

    rows = client_for(company_admin).get(MEMBERS).json()

    statuses = {row["email"]: row["status"] for row in rows}
    assert statuses["ta@velapay.vn"] == "locked"
    assert statuses["hm@velapay.vn"] == "active"


# Django admin -----------------------------------------------------------------------------------


@pytest.fixture
def admin_site() -> Client:
    User.objects.create_superuser(
        email="root@sparktal.vn", password="pass-1234-word", full_name="Sparktal Root"
    )
    client = Client()
    client.login(email="root@sparktal.vn", password="pass-1234-word")
    return client


def change_user(client: Client, user: User, **fields: str) -> Any:
    body = {"email": user.email, "full_name": user.full_name, "kind": user.kind, **fields}
    return client.post(f"/admin/accounts/user/{user.pk}/change/", body)


def test_admin_form_refuses_to_switch_a_locked_user_back_on(
    admin_site: Client, company_ta: User
) -> None:
    set_locked(company_ta.membership, locked=True)

    response = change_user(admin_site, company_ta, is_active="on")

    assert response.status_code == 200
    errors = response.context["adminform"].form.errors
    assert "is_active" in errors
    company_ta.refresh_from_db()
    assert company_ta.is_active is False
    assert company_ta.locked_at is not None


def test_admin_can_still_edit_a_locked_user(admin_site: Client, company_ta: User) -> None:
    set_locked(company_ta.membership, locked=True)

    response = change_user(admin_site, company_ta, full_name="Renamed")

    assert response.status_code == 302
    company_ta.refresh_from_db()
    assert company_ta.full_name == "Renamed"
    assert company_ta.locked_at is not None


def test_admin_shows_locked_at(admin_site: Client, company_ta: User) -> None:
    set_locked(company_ta.membership, locked=True)

    response = admin_site.get(f"/admin/accounts/user/{company_ta.pk}/change/")

    assert response.status_code == 200
    assert "locked_at" in response.context["adminform"].readonly_fields


def test_admin_lists_locked_users_by_filter(
    admin_site: Client, company_ta: User, company_admin: User
) -> None:
    set_locked(company_ta.membership, locked=True)

    response = admin_site.get("/admin/accounts/user/", {"locked_at__isempty": "0"})

    assert response.status_code == 200
    listed = {user.email for user in response.context["cl"].result_list}
    assert listed == {company_ta.email}


def unlock_action(client: Client, *users: User) -> Any:
    return client.post(
        "/admin/accounts/user/",
        {"action": "unlock_users", "_selected_action": [str(user.pk) for user in users]},
    )


def test_admin_unlock_action_restores_a_seated_user(admin_site: Client, company_ta: User) -> None:
    set_locked(company_ta.membership, locked=True)

    response = unlock_action(admin_site, company_ta)

    assert response.status_code == 302
    company_ta.refresh_from_db()
    assert company_ta.locked_at is None
    assert company_ta.is_active is True


def test_admin_unlock_action_leaves_a_removed_user_off_until_reinvited(
    admin_site: Client, company_admin: User, company_ta: User, client_for: ClientFor
) -> None:
    user = locked_and_removed(company_ta, company_admin)

    response = unlock_action(admin_site, user)

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.locked_at is None
    assert user.is_active is False
    # Unlocked, so any company may invite them again the normal way.
    assert reinvite(client_for(company_admin), user).status_code == 201
    user.refresh_from_db()
    assert user.is_active is True
