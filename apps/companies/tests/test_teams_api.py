from collections.abc import Callable
from typing import cast

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company, Team
from apps.companies.tests.factories import TeamFactory

pytestmark = pytest.mark.django_db

URL = "/api/v1/company/teams"
ClientFor = Callable[[User], APIClient]
ADMIN_ONLY = "Only a Company Admin can manage the team."


def test_admin_creates_lists_renames_deletes_team(
    company_admin: User, company_ta: User, client_for: ClientFor, company: Company
) -> None:
    client = client_for(company_admin)

    created = client.post(URL, {"name": "Growth"}, format="json")
    assert created.status_code == 201
    team_id = created.json()["id"]
    assert created.json() == {"id": team_id, "name": "Growth", "member_count": 0, "job_count": 0}

    listed = client.get(URL)
    assert listed.status_code == 200
    rows = {row["name"]: row for row in listed.json()}
    assert set(rows) == {"Growth", company_ta.membership.team.name}  # type: ignore[union-attr]
    assert rows[company_ta.membership.team.name]["member_count"] == 1  # type: ignore[union-attr]

    renamed = client.patch(f"{URL}/{team_id}", {"name": "Growth Ops"}, format="json")
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Growth Ops"

    assert client.delete(f"{URL}/{team_id}").status_code == 204
    assert not Team.objects.filter(pk=team_id).exists()


def test_team_with_members_cannot_be_deleted(
    company_admin: User, company_ta: User, client_for: ClientFor
) -> None:
    team = cast(Team, company_ta.membership.team)
    response = client_for(company_admin).delete(f"{URL}/{team.pk}")
    assert response.status_code == 400
    assert Team.objects.filter(pk=team.pk).exists()


def test_job_count_counts_only_jobs_the_viewer_can_see(
    company_admin: User,
    company_ta: User,
    company_hm: User,
    client_for: ClientFor,
    company: Company,
) -> None:
    from django.utils import timezone

    from apps.companies.models import CompanyRole
    from apps.companies.tests.factories import CompanyMembershipFactory
    from apps.jobs.models import JobStatus
    from apps.jobs.tests.factories import JobFactory

    team = cast(Team, company_ta.membership.team)
    hm_membership = company_hm.membership
    hm_membership.team = team
    hm_membership.save(update_fields=["team"])
    teammate = CompanyMembershipFactory(
        company=company, role=CompanyRole.TALENT_ACQUISITION, team=team
    ).user
    JobFactory(company=company, created_by=company_ta)
    JobFactory(company=company, created_by=company_ta, deleted_at=timezone.now())
    JobFactory(company=company, created_by=teammate, status=JobStatus.DRAFT)

    for viewer in (company_admin, company_ta, company_hm):
        rows = client_for(viewer).get(URL).json()
        own = next(r for r in rows if r["id"] == str(team.pk))
        assert own["job_count"] == 1, viewer.email
        assert own["member_count"] == 3
    # The draft's creator does see it.
    rows = client_for(teammate).get(URL).json()
    assert rows[0]["job_count"] == 2


def test_hm_lists_only_own_team(
    company_hm: User, company_ta: User, company_admin: User, client_for: ClientFor
) -> None:
    rows = client_for(company_hm).get(URL).json()
    assert [r["id"] for r in rows] == [str(company_hm.membership.team_id)]


def test_team_list_fails_closed_without_a_membership(
    company_admin: User, client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unreachable today (no membership means no company), so force the view's own lookup.
    monkeypatch.setattr("apps.companies.api.membership_of", lambda user: None)

    response = client_for(company_admin).get(URL)

    assert response.status_code == 404


def test_non_admin_cannot_create_team(company_ta: User, client_for: ClientFor) -> None:
    client = client_for(company_ta)
    response = client.post(URL, {"name": "Nope"}, format="json")
    assert response.status_code == 403
    assert response.json()["detail"] == ADMIN_ONLY
    own = company_ta.membership.team_id
    assert client.patch(f"{URL}/{own}", {"name": "X"}, format="json").status_code == 403
    assert client.delete(f"{URL}/{own}").status_code == 403


def test_other_company_team_is_404(
    other_company_admin: User, company: Company, company_admin: User, client_for: ClientFor
) -> None:
    team = cast(Team, TeamFactory(company=company))
    client = client_for(other_company_admin)
    assert client.patch(f"{URL}/{team.pk}", {"name": "X"}, format="json").status_code == 404
    assert client.delete(f"{URL}/{team.pk}").status_code == 404
    assert Team.objects.filter(pk=team.pk, name=team.name).exists()
