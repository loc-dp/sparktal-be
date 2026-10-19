import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.companies.members import MemberRuleError
from apps.companies.models import Company, CompanyRole, Team
from apps.companies.teams import TEAM_ROLES, create_team, delete_team, rename_team, resolve_team
from apps.companies.tests.factories import CompanyFactory, CompanyMembershipFactory, TeamFactory
from apps.jobs.models import JobStatus
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db


def test_team_roles_are_hm_and_ta() -> None:
    assert {CompanyRole.HIRING_MANAGER, CompanyRole.TALENT_ACQUISITION} == TEAM_ROLES


def test_create_team_strips_and_rejects_duplicates(company: Company) -> None:
    team = create_team(company, " Payments ")
    assert team.name == "Payments"
    with pytest.raises(ValidationError) as exc:
        create_team(company, "PAYMENTS")
    assert exc.value.detail == {"name": ["A team with this name already exists."]}


@pytest.mark.parametrize("name", ["", "   ", "x" * 61])
def test_create_team_rejects_bad_length(company: Company, name: str) -> None:
    with pytest.raises(ValidationError) as exc:
        create_team(company, name)
    assert "name" in exc.value.detail


def test_same_name_in_another_company_is_fine(company: Company) -> None:
    TeamFactory(company=CompanyFactory(), name="Payments")
    assert create_team(company, "Payments").name == "Payments"


def test_rename_team_rules(company: Company) -> None:
    team = TeamFactory(company=company, name="Old")
    TeamFactory(company=company, name="Taken")
    assert rename_team(team, " New ").name == "New"
    assert rename_team(team, "NEW").name == "NEW"  # renaming to own name (case) is allowed
    with pytest.raises(ValidationError):
        rename_team(team, "taken")


def test_delete_team_blocked_while_it_has_members(company: Company) -> None:
    team = TeamFactory(company=company)
    CompanyMembershipFactory(company=company, role=CompanyRole.HIRING_MANAGER, team=team)
    with pytest.raises(MemberRuleError) as exc:
        delete_team(team)
    assert exc.value.detail == "Move everyone out of this team first."


def test_delete_team_blocked_while_it_has_any_job(company: Company) -> None:
    team = TeamFactory(company=company)
    JobFactory(company=company, team=team, status=JobStatus.CLOSED, deleted_at=timezone.now())
    with pytest.raises(MemberRuleError) as exc:
        delete_team(team)
    assert exc.value.detail == "This team still has jobs. Rename it instead."


def test_delete_empty_team(company: Company) -> None:
    team = TeamFactory(company=company)
    delete_team(team)
    assert not Team.objects.filter(pk=team.pk).exists()


def test_resolve_team_rejects_other_company_team(company: Company) -> None:
    foreign = TeamFactory(company=CompanyFactory())
    with pytest.raises(ValidationError) as exc:
        resolve_team(company, team_id=foreign.pk, new_team_name=None)
    assert exc.value.detail == {"team": ["Pick a team from your company."]}


def test_resolve_team_picks_own_team(company: Company) -> None:
    team = TeamFactory(company=company)
    assert resolve_team(company, team_id=team.pk, new_team_name=None) == team


def test_resolve_team_creates_named_team(company: Company) -> None:
    team = resolve_team(company, team_id=None, new_team_name=" Growth ")
    assert team is not None
    assert team.name == "Growth"
    assert team.company == company


def test_resolve_team_with_nothing_is_none(company: Company) -> None:
    assert resolve_team(company, team_id=None, new_team_name=None) is None


def test_resolve_team_rejects_both(company: Company) -> None:
    team = TeamFactory(company=company)
    with pytest.raises(ValidationError) as exc:
        resolve_team(company, team_id=team.pk, new_team_name="Other")
    assert exc.value.detail == {"team": ["Pick a team or name a new one, not both."]}
