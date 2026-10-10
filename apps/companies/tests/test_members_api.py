from collections.abc import Callable
from typing import Any

import pytest
from django.core import mail
from rest_framework.test import APIClient

from apps.accounts.invites import InviteError
from apps.accounts.models import User, UserKind
from apps.companies.models import Company, CompanyMembership

pytestmark = pytest.mark.django_db

URL = "/api/v1/company/members"
ClientFor = Callable[[User], APIClient]


def invite(client: APIClient, **overrides: Any) -> Any:
    body = {"email": "new.hire@velapay.vn", "full_name": "New Hire", "role": "hiring_manager"}
    return client.post(URL, {**body, **overrides}, format="json")


def test_every_member_can_list_the_team(
    client_for: ClientFor, company_hm: User, company_admin: User, company_ta: User
) -> None:
    response = client_for(company_hm).get(URL)

    assert response.status_code == 200
    rows = response.json()
    assert {row["email"] for row in rows} == {
        "hm@velapay.vn",
        "admin@velapay.vn",
        "ta@velapay.vn",
    }
    me = next(row for row in rows if row["email"] == "hm@velapay.vn")
    assert me["is_me"] is True
    assert me["status"] == "active"
    assert me["role"] == "hiring_manager"


def test_list_never_includes_another_company(
    client_for: ClientFor, company_admin: User, other_company_admin: User
) -> None:
    emails = {row["email"] for row in client_for(company_admin).get(URL).json()}
    assert "admin@lumen.vn" not in emails


def test_recruiter_has_no_team(client_for: ClientFor, recruiter_user: User) -> None:
    assert client_for(recruiter_user).get(URL).status_code == 404


def test_admin_invites_a_member_and_an_email_goes_out(
    client_for: ClientFor, company_admin: User, company: Company
) -> None:
    response = invite(client_for(company_admin))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "invited"
    assert body["role"] == "hiring_manager"
    user = User.objects.get(email="new.hire@velapay.vn")
    assert user.kind == UserKind.COMPANY
    assert not user.has_usable_password()
    assert user.membership.company == company
    assert [m.to for m in mail.outbox] == [["new.hire@velapay.vn"]]


@pytest.mark.parametrize("actor", ["company_ta", "company_hm"])
def test_only_admin_can_invite(
    request: pytest.FixtureRequest, client_for: ClientFor, actor: str
) -> None:
    user = request.getfixturevalue(actor)
    assert invite(client_for(user)).status_code == 403
    assert not User.objects.filter(email="new.hire@velapay.vn").exists()
    assert mail.outbox == []


@pytest.mark.parametrize(
    "taken",
    ["admin@velapay.vn", "ADMIN@VelaPay.vn", "admin@lumen.vn"],
    ids=["same-company", "different-case", "other-company"],
)
def test_email_already_in_use_is_rejected(
    client_for: ClientFor, company_admin: User, other_company_admin: User, taken: str
) -> None:
    users, memberships = User.objects.count(), CompanyMembership.objects.count()

    response = invite(client_for(company_admin), email=taken)

    assert response.status_code == 400
    assert "email" in response.json()
    assert mail.outbox == []
    assert User.objects.count() == users
    assert CompanyMembership.objects.count() == memberships


def test_recruiter_email_is_rejected(
    client_for: ClientFor, company_admin: User, recruiter_user: User
) -> None:
    users, memberships = User.objects.count(), CompanyMembership.objects.count()

    response = invite(client_for(company_admin), email=recruiter_user.email)

    assert response.status_code == 400
    assert "email" in response.json()
    assert mail.outbox == []
    assert User.objects.count() == users
    assert CompanyMembership.objects.count() == memberships


@pytest.mark.parametrize(
    "bad",
    [{"email": "not-an-email"}, {"full_name": "   "}, {"role": "owner"}],
    ids=["email", "name", "role"],
)
def test_invalid_payload_is_rejected(
    client_for: ClientFor, company_admin: User, bad: dict[str, str]
) -> None:
    assert invite(client_for(company_admin), **bad).status_code == 400


def make_inactive(email: str, *, kind: str = UserKind.COMPANY) -> User:
    user = User.objects.create_user(email=email, password=None, full_name="Gone", kind=kind)
    user.is_active = False
    user.save()
    return user


def test_reinviting_a_removed_member_reactivates_the_same_user(
    client_for: ClientFor, company_admin: User, company: Company
) -> None:
    former = make_inactive("gone@velapay.vn")
    former.set_password("old-secret-123")
    former.save()

    response = invite(
        client_for(company_admin),
        email="gone@velapay.vn",
        full_name="Back Again",
        role="talent_acquisition",
    )

    assert response.status_code == 201
    assert response.json()["role"] == "talent_acquisition"
    assert response.json()["status"] == "invited"
    former.refresh_from_db()
    assert former.is_active is True
    assert former.full_name == "Back Again"
    assert not former.has_usable_password()
    memberships = CompanyMembership.objects.filter(user=former)
    assert memberships.count() == 1
    assert memberships.get().company == company
    assert memberships.get().role == "talent_acquisition"
    assert User.objects.filter(email="gone@velapay.vn").count() == 1
    assert [m.to for m in mail.outbox] == [["gone@velapay.vn"]]


@pytest.mark.parametrize("kind", [UserKind.RECRUITER, UserKind.STAFF])
def test_inactive_non_company_user_is_rejected(
    client_for: ClientFor, company_admin: User, kind: str
) -> None:
    other = make_inactive("ex@elsewhere.vn", kind=kind)
    memberships = CompanyMembership.objects.count()

    response = invite(client_for(company_admin), email="ex@elsewhere.vn")

    assert response.status_code == 400
    assert "email" in response.json()
    assert mail.outbox == []
    other.refresh_from_db()
    assert other.is_active is False
    assert other.full_name == "Gone"
    assert other.kind == kind
    assert CompanyMembership.objects.count() == memberships


def test_inactive_user_who_still_has_a_membership_is_rejected(
    client_for: ClientFor, company_admin: User, other_company_admin: User
) -> None:
    other_company_admin.is_active = False
    other_company_admin.save()
    membership = other_company_admin.membership
    memberships = CompanyMembership.objects.count()

    response = invite(client_for(company_admin), email=other_company_admin.email)

    assert response.status_code == 400
    assert "email" in response.json()
    assert mail.outbox == []
    other_company_admin.refresh_from_db()
    assert other_company_admin.is_active is False
    assert CompanyMembership.objects.count() == memberships
    membership.refresh_from_db()
    assert membership.company_id == other_company_admin.membership.company_id


@pytest.fixture
def smtp_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(email: str) -> None:
        raise InviteError("smtp down")

    monkeypatch.setattr("apps.companies.api.send_password_invite", boom)


@pytest.mark.usefixtures("smtp_down")
def test_a_failed_invite_email_rolls_everything_back(
    client_for: ClientFor, company_admin: User
) -> None:
    memberships = CompanyMembership.objects.count()

    response = invite(client_for(company_admin))

    assert response.status_code == 502
    assert "detail" in response.json()
    assert not User.objects.filter(email="new.hire@velapay.vn").exists()
    assert CompanyMembership.objects.count() == memberships


@pytest.mark.usefixtures("smtp_down")
def test_a_failed_invite_email_rolls_back_a_reactivation(
    client_for: ClientFor, company_admin: User
) -> None:
    former = make_inactive("gone@velapay.vn")

    response = invite(client_for(company_admin), email="gone@velapay.vn", full_name="Back Again")

    assert response.status_code == 502
    former.refresh_from_db()
    assert former.is_active is False
    assert former.full_name == "Gone"
    assert not CompanyMembership.objects.filter(user=former).exists()
