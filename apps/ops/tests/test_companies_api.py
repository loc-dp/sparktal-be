"""Sparktal ops: companies, their members and teams, and locking an account."""

import smtplib
from collections.abc import Callable
from typing import Any, cast
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.invites import InviteError
from apps.accounts.models import User, UserKind
from apps.accounts.tests.factories import DEFAULT_TEST_PASSWORD, UserFactory
from apps.companies.members import ACCOUNT_LOCKED, EMAIL_TAKEN, LAST_ADMIN
from apps.companies.models import Company, CompanyMembership, CompanyRole, Team
from apps.companies.tests.factories import CompanyFactory, CompanyMembershipFactory, TeamFactory
from apps.jobs.models import Job, JobStatus
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db

ClientFor = Callable[[User], APIClient]

COMPANIES = "/api/v1/ops/companies"
ME = "/api/v1/me"
LOGIN = "/_allauth/browser/v1/auth/login"
INVITE_FAILED = "We couldn't send the invite email, so nothing was saved. Please try again."
LOCKED_NO_INVITE = "This account is locked. Unlock it before sending the invite again."
NAME_LENGTH = "Give the company a name of 1 to 120 characters."

ROW_KEYS = {
    "id",
    "name",
    "created_at",
    "profile_completed",
    "members",
    "teams",
    "active_jobs",
    "queued_jobs",
}


def company_url(company: Company, suffix: str = "") -> str:
    return f"{COMPANIES}/{company.pk}{suffix}"


def member_url(company: Company, user: User, suffix: str = "") -> str:
    return company_url(company, f"/members/{user.membership.pk}{suffix}")


def create(client: APIClient, **overrides: Any) -> Any:
    body = {
        "name": "Nordhaven",
        "admin_email": "admin@nordhaven.vn",
        "admin_full_name": "Phạm Quốc Anh",
    }
    return client.post(COMPANIES, {**body, **overrides}, format="json")


@pytest.fixture
def smtp_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(email: str, **details: str | None) -> None:
        raise InviteError("smtp down")

    monkeypatch.setattr("apps.companies.api.send_password_invite", boom)


@pytest.fixture
def smtp_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mail server itself fails: nothing between the view and SMTP is patched."""

    def refuse(self: object, messages: object) -> int:
        raise smtplib.SMTPException("421 Service not available")

    monkeypatch.setattr("django.core.mail.backends.locmem.EmailBackend.send_messages", refuse)


# Create a company ----------------------------------------------------------------------------


def test_create_company_sends_admin_invite(staff_client: APIClient, mailoutbox: list[Any]) -> None:
    response = create(staff_client, name="  Nordhaven  ")

    assert response.status_code == 201
    body = response.json()
    assert set(body) == ROW_KEYS
    company = Company.objects.get(pk=body["id"])
    assert company.name == "Nordhaven"
    assert body["name"] == "Nordhaven"
    assert body["profile_completed"] is False
    assert (body["members"], body["teams"], body["active_jobs"], body["queued_jobs"]) == (
        1,
        0,
        0,
        0,
    )
    membership = company.memberships.select_related("user").get()
    assert membership.role == CompanyRole.ADMIN
    assert membership.team is None
    admin = membership.user
    assert admin.email == "admin@nordhaven.vn"
    assert admin.full_name == "Phạm Quốc Anh"
    assert admin.kind == UserKind.COMPANY
    assert admin.is_active is True
    assert not admin.has_usable_password()
    assert [m.to for m in mailoutbox] == [["admin@nordhaven.vn"]]
    assert "Nordhaven" in mailoutbox[0].subject
    assert "The Sparktal team has invited you to join Nordhaven" in mailoutbox[0].body
    assert "reset-password?key=" in mailoutbox[0].body


@pytest.mark.parametrize(
    "taken",
    ["northstar@example.com", "NorthStar@Example.com", "admin@lumen.vn", "ops@sparktal.com"],
    ids=["recruiter", "recruiter-other-case", "other-company-member", "staff"],
)
def test_create_company_with_taken_email(
    staff_client: APIClient,
    recruiter_profile: Any,
    other_company_admin: User,
    mailoutbox: list[Any],
    taken: str,
) -> None:
    companies, users = Company.objects.count(), User.objects.count()

    response = create(staff_client, admin_email=taken)

    assert response.status_code == 400
    assert response.json() == {"admin_email": [EMAIL_TAKEN]}
    assert Company.objects.count() == companies
    assert not Company.objects.filter(name="Nordhaven").exists()
    assert User.objects.count() == users
    assert mailoutbox == []


def test_create_company_with_a_removed_users_email_is_still_taken(
    staff_client: APIClient, mailoutbox: list[Any]
) -> None:
    UserFactory(email="gone@nordhaven.vn", is_active=False)

    response = create(staff_client, admin_email="gone@nordhaven.vn")

    assert response.status_code == 400
    assert response.json() == {"admin_email": [EMAIL_TAKEN]}
    assert not Company.objects.filter(name="Nordhaven").exists()


@pytest.mark.usefixtures("smtp_down")
def test_create_company_rolls_back_when_invite_fails(staff_client: APIClient) -> None:
    companies, users = Company.objects.count(), User.objects.count()
    memberships = CompanyMembership.objects.count()

    response = create(staff_client)

    assert response.status_code == 502
    assert response.json() == {"detail": INVITE_FAILED}
    assert Company.objects.count() == companies
    assert not Company.objects.filter(name="Nordhaven").exists()
    assert User.objects.count() == users
    assert not User.objects.filter(email="admin@nordhaven.vn").exists()
    assert CompanyMembership.objects.count() == memberships


@pytest.mark.usefixtures("smtp_refuses")
def test_create_company_answers_502_when_the_mail_server_fails(staff_client: APIClient) -> None:
    response = create(staff_client)

    assert response.status_code == 502
    assert response.json() == {"detail": INVITE_FAILED}
    assert not Company.objects.filter(name="Nordhaven").exists()
    assert not User.objects.filter(email="admin@nordhaven.vn").exists()


@pytest.mark.parametrize("name", ["", "   ", "x" * 121])
def test_create_company_name_is_1_to_120_characters(staff_client: APIClient, name: str) -> None:
    response = create(staff_client, name=name)

    assert response.status_code == 400
    assert response.json() == {"name": [NAME_LENGTH]}
    assert not User.objects.filter(email="admin@nordhaven.vn").exists()


def test_create_company_name_of_120_characters_is_fine(staff_client: APIClient) -> None:
    response = create(staff_client, name="x" * 120)

    assert response.status_code == 201
    assert response.json()["name"] == "x" * 120


@pytest.mark.parametrize(
    ("field", "value"),
    [("admin_email", "not-an-email"), ("admin_email", ""), ("admin_full_name", "")],
)
def test_create_company_needs_an_admin_email_and_name(
    staff_client: APIClient, field: str, value: str
) -> None:
    response = create(staff_client, **{field: value})

    assert response.status_code == 400
    assert field in response.json()
    assert not Company.objects.filter(name="Nordhaven").exists()


# List and read companies ---------------------------------------------------------------------


def test_company_row_counts(staff_client: APIClient, company: Company) -> None:
    payments = cast(Team, TeamFactory(company=company, name="Payments"))
    TeamFactory(company=company, name="Growth")
    CompanyMembershipFactory(company=company, role=CompanyRole.ADMIN)
    CompanyMembershipFactory(company=company, role=CompanyRole.HIRING_MANAGER, team=payments)
    for status in (JobStatus.ACTIVE, JobStatus.ACTIVE, JobStatus.PENDING, JobStatus.CLOSED):
        JobFactory(company=company, team=payments, status=status)
    JobFactory(company=company, team=payments, status=JobStatus.DRAFT)
    # Soft-deleted jobs are gone, whatever their status says.
    JobFactory(company=company, team=payments, status=JobStatus.ACTIVE, deleted_at=timezone.now())
    JobFactory(company=company, team=payments, status=JobStatus.PENDING, deleted_at=timezone.now())
    # Another company's numbers stay out of this row.
    other = cast(Company, CompanyFactory(name="Lumen Retail"))
    JobFactory(company=other, status=JobStatus.PENDING)

    row = staff_client.get(company_url(company)).json()

    assert set(row) == ROW_KEYS
    assert row["id"] == str(company.pk)
    assert row["name"] == "Vela Pay"
    assert row["profile_completed"] is True
    assert row["members"] == 2
    assert row["teams"] == 2
    assert row["active_jobs"] == 2
    assert row["queued_jobs"] == 1


def test_company_list_is_paginated_and_searchable(staff_client: APIClient) -> None:
    CompanyFactory(name="Vela Pay")
    CompanyFactory(name="Lumen Retail")
    CompanyFactory(name="Kestrel Logistics", profile_completed_at=None)

    everything = staff_client.get(COMPANIES).json()
    assert everything["count"] == 3
    assert [row["name"] for row in everything["results"]] == [
        "Kestrel Logistics",
        "Lumen Retail",
        "Vela Pay",
    ]
    assert all(set(row) == ROW_KEYS for row in everything["results"])
    kestrel = everything["results"][0]
    assert kestrel["profile_completed"] is False
    assert kestrel["members"] == 0

    found = staff_client.get(COMPANIES, {"search": "  lumen "}).json()
    assert [row["name"] for row in found["results"]] == ["Lumen Retail"]
    assert staff_client.get(COMPANIES, {"search": "nothing like it"}).json()["count"] == 0


def test_company_list_queries_do_not_grow_with_the_page(
    staff_client: APIClient, django_assert_max_num_queries: Any
) -> None:
    for index in range(12):
        company = cast(Company, CompanyFactory(name=f"Company {index:02d}"))
        team = cast(Team, TeamFactory(company=company))
        CompanyMembershipFactory(company=company, role=CompanyRole.HIRING_MANAGER, team=team)
        CompanyMembershipFactory(company=company, role=CompanyRole.TALENT_ACQUISITION, team=team)
        JobFactory(company=company, team=team, status=JobStatus.ACTIVE)
        JobFactory(company=company, team=team, status=JobStatus.PENDING)

    # Session, user, the count and the page; none of them per company.
    with django_assert_max_num_queries(4):
        rows = staff_client.get(COMPANIES).json()["results"]

    assert len(rows) == 12
    assert {(r["members"], r["teams"], r["active_jobs"], r["queued_jobs"]) for r in rows} == {
        (2, 1, 1, 1)
    }


def test_ops_unknown_company_is_404(staff_client: APIClient) -> None:
    missing = f"{COMPANIES}/{uuid4()}"
    some_id = uuid4()
    requests = [
        ("get", ""),
        ("get", "/members"),
        ("post", "/members"),
        ("patch", f"/members/{some_id}"),
        ("delete", f"/members/{some_id}"),
        ("post", f"/members/{some_id}/resend-invite"),
        ("post", f"/members/{some_id}/lock"),
        ("post", f"/members/{some_id}/unlock"),
        ("get", "/teams"),
        ("post", "/teams"),
        ("patch", f"/teams/{some_id}"),
        ("delete", f"/teams/{some_id}"),
    ]
    for method, path in requests:
        body = {"name": "Growth", "email": "x@x.vn", "full_name": "X", "role": "admin"}
        response = getattr(staff_client, method)(missing + path, body, format="json")
        assert response.status_code == 404, (method, path, response.status_code)
    assert not User.objects.filter(email="x@x.vn").exists()
    assert not Team.objects.filter(name="Growth").exists()


def test_ops_unknown_member_or_team_is_404(staff_client: APIClient, company: Company) -> None:
    for method, path in [
        ("patch", f"/members/{uuid4()}"),
        ("delete", f"/members/{uuid4()}"),
        ("post", f"/members/{uuid4()}/resend-invite"),
        ("post", f"/members/{uuid4()}/lock"),
        ("post", f"/members/{uuid4()}/unlock"),
        ("patch", f"/teams/{uuid4()}"),
        ("delete", f"/teams/{uuid4()}"),
    ]:
        response = getattr(staff_client, method)(company_url(company, path), {}, format="json")
        assert response.status_code == 404, (method, path)


def test_member_or_team_of_another_company_is_404(
    staff_client: APIClient, company: Company, other_company_admin: User
) -> None:
    other = other_company_admin.membership.company
    other_team = cast(Team, TeamFactory(company=other, name="Lumen Ops"))
    other_hm = cast(
        CompanyMembership,
        CompanyMembershipFactory(company=other, role=CompanyRole.HIRING_MANAGER, team=other_team),
    ).user
    mine = cast(Team, TeamFactory(company=company, name="Payments"))

    for user in (other_company_admin, other_hm):
        assert (
            staff_client.patch(
                member_url(company, user),
                {"role": "talent_acquisition", "team": str(mine.pk)},
                format="json",
            ).status_code
            == 404
        )
        assert staff_client.delete(member_url(company, user)).status_code == 404
        assert staff_client.post(member_url(company, user, "/resend-invite")).status_code == 404
        assert staff_client.post(member_url(company, user, "/lock")).status_code == 404
        assert staff_client.post(member_url(company, user, "/unlock")).status_code == 404
    team_url = company_url(company, f"/teams/{other_team.pk}")
    assert staff_client.patch(team_url, {"name": "Taken"}, format="json").status_code == 404
    assert staff_client.delete(team_url).status_code == 404

    other_company_admin.refresh_from_db()
    assert other_company_admin.is_active is True
    assert other_company_admin.membership.role == CompanyRole.ADMIN
    assert CompanyMembership.objects.filter(user=other_hm).exists()
    other_team.refresh_from_db()
    assert other_team.name == "Lumen Ops"


# Staff only ----------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["anonymous", "company_admin", "recruiter_user"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", ""),
        ("post", ""),
        ("get", "/{company}"),
        ("get", "/{company}/members"),
        ("post", "/{company}/members"),
        ("patch", "/{company}/members/{member}"),
        ("delete", "/{company}/members/{member}"),
        ("post", "/{company}/members/{member}/resend-invite"),
        ("post", "/{company}/members/{member}/lock"),
        ("post", "/{company}/members/{member}/unlock"),
        ("get", "/{company}/teams"),
        ("post", "/{company}/teams"),
        ("patch", "/{company}/teams/{team}"),
        ("delete", "/{company}/teams/{team}"),
    ],
)
def test_ops_companies_are_staff_only(
    who: str,
    method: str,
    path: str,
    api_client: APIClient,
    company: Company,
    company_admin: User,
    company_ta: User,
    request: pytest.FixtureRequest,
    mailoutbox: list[Any],
) -> None:
    team = company_ta.membership.team
    assert team is not None
    if who != "anonymous":
        api_client.force_login(request.getfixturevalue(who))
    url = COMPANIES + path.format(company=company.pk, member=company_ta.membership.pk, team=team.pk)
    body = {
        "name": "Nordhaven",
        "admin_email": "admin@nordhaven.vn",
        "admin_full_name": "Anh",
        "email": "new@velapay.vn",
        "full_name": "New",
        "role": "admin",
    }

    response = getattr(api_client, method)(url, body, format="json")

    assert response.status_code == 403
    assert not Company.objects.filter(name="Nordhaven").exists()
    assert not User.objects.filter(email__in=["admin@nordhaven.vn", "new@velapay.vn"]).exists()
    assert not Team.objects.filter(name="Nordhaven").exists()
    company_ta.refresh_from_db()
    assert company_ta.is_active is True
    assert company_ta.membership.role == CompanyRole.TALENT_ACQUISITION
    team.refresh_from_db()
    assert team.name != "Nordhaven"
    assert mailoutbox == []


# Members -------------------------------------------------------------------------------------


def test_ops_member_list_shows_every_team(
    staff_client: APIClient,
    company: Company,
    company_admin: User,
    company_ta: User,
    company_hm: User,
    other_company_admin: User,
) -> None:
    assert company_ta.membership.team_id != company_hm.membership.team_id

    response = staff_client.get(company_url(company, "/members"))

    assert response.status_code == 200
    rows = response.json()
    assert {row["email"] for row in rows} == {
        "admin@velapay.vn",
        "ta@velapay.vn",
        "hm@velapay.vn",
    }
    assert all(row["is_me"] is False for row in rows)
    teams = staff_client.get(company_url(company, "/teams")).json()
    assert {row["id"] for row in teams} == {
        str(company_ta.membership.team_id),
        str(company_hm.membership.team_id),
    }


def test_ops_invites_admin_and_ta_with_new_team(
    staff_client: APIClient, company: Company, mailoutbox: list[Any]
) -> None:
    admin = staff_client.post(
        company_url(company, "/members"),
        {"email": "boss@velapay.vn", "full_name": "Boss", "role": "admin"},
        format="json",
    )

    assert admin.status_code == 201
    assert admin.json()["role"] == "admin"
    assert admin.json()["team"] is None
    assert admin.json()["status"] == "invited"
    assert admin.json()["is_me"] is False
    boss = User.objects.get(email="boss@velapay.vn")
    assert boss.kind == UserKind.COMPANY
    assert boss.membership.company == company
    assert boss.membership.role == CompanyRole.ADMIN

    ta = staff_client.post(
        company_url(company, "/members"),
        {
            "email": "recruit@velapay.vn",
            "full_name": "Recruit",
            "role": "talent_acquisition",
            "new_team_name": "Growth",
            "can_publish": True,
        },
        format="json",
    )

    assert ta.status_code == 201
    assert ta.json()["team"]["name"] == "Growth"
    assert ta.json()["can_publish"] is True
    growth = Team.objects.get(company=company, name="Growth")
    assert User.objects.get(email="recruit@velapay.vn").membership.team == growth
    assert [m.to for m in mailoutbox] == [["boss@velapay.vn"], ["recruit@velapay.vn"]]
    for message in mailoutbox:
        assert "The Sparktal team has invited you to join Vela Pay" in message.body


@pytest.mark.usefixtures("smtp_down")
def test_ops_invite_rolls_back_when_the_email_fails(
    staff_client: APIClient, company: Company
) -> None:
    response = staff_client.post(
        company_url(company, "/members"),
        {
            "email": "recruit@velapay.vn",
            "full_name": "Recruit",
            "role": "hiring_manager",
            "new_team_name": "Growth",
        },
        format="json",
    )

    assert response.status_code == 502
    assert not User.objects.filter(email="recruit@velapay.vn").exists()
    assert not Team.objects.filter(name="Growth").exists()


def test_ops_invite_with_a_taken_email(
    staff_client: APIClient, company: Company, recruiter_user: User
) -> None:
    response = staff_client.post(
        company_url(company, "/members"),
        {"email": recruiter_user.email, "full_name": "X", "role": "admin"},
        format="json",
    )

    assert response.status_code == 400
    assert response.json() == {"email": [EMAIL_TAKEN]}


def test_ops_cannot_invite_a_locked_removed_user_back(
    staff_client: APIClient,
    company: Company,
    company_ta: User,
    other_company_admin: User,
    mailoutbox: list[Any],
) -> None:
    staff_client.post(member_url(company, company_ta, "/lock"))
    assert staff_client.delete(member_url(company, company_ta)).status_code == 204
    other = other_company_admin.membership.company
    mailoutbox.clear()

    for target in (company, other):
        response = staff_client.post(
            company_url(target, "/members"),
            {"email": company_ta.email, "full_name": "Back", "role": "admin"},
            format="json",
        )

        assert response.status_code == 400
        assert response.json() == {"email": [ACCOUNT_LOCKED]}
    company_ta.refresh_from_db()
    assert company_ta.is_active is False
    assert company_ta.locked_at is not None
    assert not CompanyMembership.objects.filter(user=company_ta).exists()
    assert mailoutbox == []


def test_ops_invite_cannot_use_another_companys_team(
    staff_client: APIClient, company: Company, other_company_admin: User
) -> None:
    other_team = cast(Team, TeamFactory(company=other_company_admin.membership.company))

    response = staff_client.post(
        company_url(company, "/members"),
        {
            "email": "recruit@velapay.vn",
            "full_name": "Recruit",
            "role": "hiring_manager",
            "team": str(other_team.pk),
        },
        format="json",
    )

    assert response.status_code == 400
    assert "team" in response.json()
    assert not User.objects.filter(email="recruit@velapay.vn").exists()


def test_ops_moves_member_between_teams(
    staff_client: APIClient, company: Company, company_ta: User
) -> None:
    created = staff_client.post(company_url(company, "/teams"), {"name": "Platform"}, format="json")
    assert created.status_code == 201
    platform = created.json()

    moved = staff_client.patch(
        member_url(company, company_ta), {"team": platform["id"]}, format="json"
    )

    assert moved.status_code == 200
    assert moved.json()["team"] == {"id": platform["id"], "name": "Platform"}
    company_ta.membership.refresh_from_db()
    assert str(company_ta.membership.team_id) == platform["id"]

    again = staff_client.patch(
        member_url(company, company_ta),
        {"role": "hiring_manager", "new_team_name": "Data"},
        format="json",
    )

    assert again.status_code == 200
    assert again.json()["role"] == "hiring_manager"
    assert again.json()["team"]["name"] == "Data"
    assert Team.objects.filter(company=company, name="Data").exists()


def test_ops_makes_a_member_admin(
    staff_client: APIClient, company: Company, company_admin: User, company_hm: User
) -> None:
    response = staff_client.patch(member_url(company, company_hm), {"role": "admin"}, format="json")

    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert response.json()["team"] is None


def test_ops_removes_a_member(staff_client: APIClient, company: Company, company_hm: User) -> None:
    response = staff_client.delete(member_url(company, company_hm))

    assert response.status_code == 204
    company_hm.refresh_from_db()
    assert company_hm.is_active is False
    assert not CompanyMembership.objects.filter(user=company_hm).exists()


def test_ops_cannot_remove_or_demote_the_last_admin(
    staff_client: APIClient, company: Company, company_admin: User
) -> None:
    removed = staff_client.delete(member_url(company, company_admin))
    demoted = staff_client.patch(
        member_url(company, company_admin),
        {"role": "hiring_manager", "new_team_name": "Ops"},
        format="json",
    )

    assert removed.status_code == 400
    assert removed.json() == {"detail": LAST_ADMIN}
    assert demoted.status_code == 400
    assert demoted.json() == {"detail": LAST_ADMIN}
    assert CompanyMembership.objects.get(user=company_admin).role == CompanyRole.ADMIN


def test_ops_resends_an_invite(
    staff_client: APIClient, company: Company, company_hm: User, mailoutbox: list[Any]
) -> None:
    staff_client.post(
        company_url(company, "/members"),
        {"email": "p@velapay.vn", "full_name": "P", "role": "admin"},
        format="json",
    )
    mailoutbox.clear()
    pending = User.objects.get(email="p@velapay.vn")

    sent = staff_client.post(member_url(company, pending, "/resend-invite"))
    joined = staff_client.post(member_url(company, company_hm, "/resend-invite"))

    assert sent.status_code == 204
    assert joined.status_code == 400
    assert [m.to for m in mailoutbox] == [["p@velapay.vn"]]
    assert "The Sparktal team has invited you" in mailoutbox[0].body


# Teams ---------------------------------------------------------------------------------------


def test_ops_creates_renames_and_deletes_a_team(staff_client: APIClient, company: Company) -> None:
    teams = company_url(company, "/teams")

    created = staff_client.post(teams, {"name": "  Growth "}, format="json")
    assert created.status_code == 201
    team = created.json()
    assert team == {"id": team["id"], "name": "Growth", "member_count": 0, "job_count": 0}
    assert staff_client.post(teams, {"name": "growth"}, format="json").status_code == 400

    renamed = staff_client.patch(f"{teams}/{team['id']}", {"name": "Marketing"}, format="json")
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Marketing"

    assert staff_client.delete(f"{teams}/{team['id']}").status_code == 204
    assert not Team.objects.filter(pk=team["id"]).exists()


def test_ops_team_rules_still_hold(
    staff_client: APIClient, company: Company, company_ta: User
) -> None:
    team = company_ta.membership.team
    assert team is not None
    url = company_url(company, f"/teams/{team.pk}")

    response = staff_client.delete(url)

    assert response.status_code == 400
    assert response.json() == {"detail": "Move everyone out of this team first."}
    assert Team.objects.filter(pk=team.pk).exists()


def test_ops_team_counts_every_posted_job(
    staff_client: APIClient, company: Company, company_ta: User
) -> None:
    team = company_ta.membership.team
    for status in (JobStatus.PENDING, JobStatus.ACTIVE, JobStatus.CLOSED):
        JobFactory(company=company, team=team, status=status)
    # Not Sparktal's to see: a draft, a job still with the company, a deleted job.
    JobFactory(company=company, team=team, status=JobStatus.DRAFT, created_by=company_ta)
    JobFactory(company=company, team=team, status=JobStatus.SUBMITTED, created_by=company_ta)
    JobFactory(company=company, team=team, status=JobStatus.ACTIVE, deleted_at=timezone.now())

    rows = staff_client.get(company_url(company, "/teams")).json()

    assert team is not None
    assert rows == [{"id": str(team.pk), "name": team.name, "member_count": 1, "job_count": 3}]


# Lock and unlock -----------------------------------------------------------------------------


def test_locked_member_is_signed_out(
    staff_client: APIClient, company: Company, company_ta: User, client_for: ClientFor
) -> None:
    ta_client = client_for(company_ta)
    assert ta_client.get(ME).status_code == 200

    locked = staff_client.post(member_url(company, company_ta, "/lock"))

    assert locked.status_code == 200
    assert locked.json()["status"] == "locked"
    # The very next request: the session no longer has a user behind it. Anonymous is 403 here
    # (session auth sends no WWW-Authenticate challenge, so DRF never answers 401).
    assert ta_client.get(ME).status_code == 403
    credentials = {"email": company_ta.email, "password": DEFAULT_TEST_PASSWORD}
    assert ta_client.post(LOGIN, credentials, format="json").status_code == 401
    assert ta_client.get(ME).status_code == 403

    unlocked = staff_client.post(member_url(company, company_ta, "/unlock"))

    assert unlocked.status_code == 200
    assert unlocked.json()["status"] == "active"
    # The session open when the account was locked stays gone: they sign in again.
    assert ta_client.get(ME).status_code == 403
    assert ta_client.post(LOGIN, credentials, format="json").status_code == 200
    me = ta_client.get(ME)
    assert me.status_code == 200
    assert me.json()["email"] == "ta@velapay.vn"


def test_lock_keeps_the_seat_the_team_and_the_jobs(
    staff_client: APIClient, company: Company, company_ta: User
) -> None:
    membership = company_ta.membership
    team_id = membership.team_id
    job = cast(Job, JobFactory(company=company, created_by=company_ta, status=JobStatus.DRAFT))

    staff_client.post(member_url(company, company_ta, "/lock"))
    staff_client.post(member_url(company, company_ta, "/lock"))  # twice is still locked

    membership.refresh_from_db()
    assert membership.team_id == team_id
    assert membership.role == CompanyRole.TALENT_ACQUISITION
    job.refresh_from_db()
    assert job.created_by_id == company_ta.pk
    assert job.team_id == team_id
    company_ta.refresh_from_db()
    assert company_ta.is_active is False


def test_the_last_admin_can_be_locked(
    staff_client: APIClient, company: Company, company_admin: User
) -> None:
    response = staff_client.post(member_url(company, company_admin, "/lock"))

    assert response.status_code == 200
    assert response.json()["status"] == "locked"
    assert response.json()["role"] == "admin"
    company_admin.refresh_from_db()
    assert company_admin.is_active is False


def test_member_status_locked_on_company_team_page(
    staff_client: APIClient,
    company: Company,
    company_admin: User,
    company_ta: User,
    company_hm: User,
    client_for: ClientFor,
) -> None:
    staff_client.post(
        company_url(company, "/members"),
        {"email": "p@velapay.vn", "full_name": "P", "role": "admin"},
        format="json",
    )
    invited = User.objects.get(email="p@velapay.vn")
    staff_client.post(member_url(company, company_ta, "/lock"))
    staff_client.post(member_url(company, invited, "/lock"))  # locked wins over invited

    rows = client_for(company_admin).get("/api/v1/company/members").json()

    assert {row["email"]: row["status"] for row in rows} == {
        "admin@velapay.vn": "active",
        "ta@velapay.vn": "locked",
        "hm@velapay.vn": "active",
        "p@velapay.vn": "locked",
    }
    ops_rows = staff_client.get(company_url(company, "/members")).json()
    assert {row["email"]: row["status"] for row in ops_rows}["ta@velapay.vn"] == "locked"


@pytest.mark.parametrize(
    ("side", "detail"),
    # Only Sparktal can unlock, so only Sparktal is told to; the company sees the Locked pill.
    [("ops", LOCKED_NO_INVITE), ("company", ACCOUNT_LOCKED)],
)
def test_a_locked_invitee_gets_no_invite_email(
    side: str,
    detail: str,
    staff_client: APIClient,
    company: Company,
    company_admin: User,
    client_for: ClientFor,
    mailoutbox: list[Any],
) -> None:
    staff_client.post(
        company_url(company, "/members"),
        {"email": "p@velapay.vn", "full_name": "P", "role": "admin"},
        format="json",
    )
    invited = User.objects.get(email="p@velapay.vn")
    staff_client.post(member_url(company, invited, "/lock"))
    mailoutbox.clear()
    if side == "ops":
        response = staff_client.post(member_url(company, invited, "/resend-invite"))
    else:
        url = f"/api/v1/company/members/{invited.membership.pk}/resend-invite"
        response = client_for(company_admin).post(url)

    assert response.status_code == 400
    assert response.json() == {"detail": detail}
    assert mailoutbox == []
