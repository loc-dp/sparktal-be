from uuid import UUID

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from apps.companies.members import MemberRuleError
from apps.companies.models import Company, CompanyMembership, CompanyRole, Team
from apps.jobs.models import Job

TEAM_ROLES = frozenset({CompanyRole.HIRING_MANAGER, CompanyRole.TALENT_ACQUISITION})

NAME_TAKEN = "A team with this name already exists."
NAME_LENGTH = "Give the team a name of 1 to 60 characters."


def _clean_name(company: Company, name: str, *, exclude: Team | None = None) -> str:
    cleaned = name.strip()
    if not 1 <= len(cleaned) <= 60:
        raise ValidationError({"name": [NAME_LENGTH]})
    taken = Team.objects.filter(company=company, name__iexact=cleaned)
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    if taken.exists():
        raise ValidationError({"name": [NAME_TAKEN]})
    return cleaned


def create_team(company: Company, name: str) -> Team:
    cleaned = _clean_name(company, name)
    try:
        with transaction.atomic():
            return Team.objects.create(company=company, name=cleaned)
    except IntegrityError as exc:  # lost a race against the unique constraint
        raise ValidationError({"name": [NAME_TAKEN]}) from exc


def rename_team(team: Team, name: str) -> Team:
    team.name = _clean_name(team.company, name, exclude=team)
    try:
        with transaction.atomic():
            team.save(update_fields=["name"])
    except IntegrityError as exc:
        raise ValidationError({"name": [NAME_TAKEN]}) from exc
    return team


@transaction.atomic
def delete_team(team: Team) -> None:
    """Delete a team nobody and no job is in. A team already gone is left at that.

    The team row is locked until commit, so two deletes or a rename of it wait their turn. Moving
    a member or a job into the team takes no lock on it: its foreign key is checked when it
    commits. One that has not committed by the checks below is not seen; the database then
    refuses its commit, as the team is gone, so nothing is left pointing at no team.
    """
    locked = Team.objects.select_for_update().filter(pk=team.pk).first()
    if locked is None:
        return
    if CompanyMembership.objects.filter(team=locked).exists():
        raise MemberRuleError("Move everyone out of this team first.")
    # Default manager on purpose: closed and soft-deleted jobs still point at the team.
    if Job.objects.filter(team=locked).exists():
        raise MemberRuleError("This team still has jobs. Rename it instead.")
    locked.delete()


def resolve_team(
    company: Company, *, team_id: UUID | None, new_team_name: str | None
) -> Team | None:
    if team_id is not None and new_team_name is not None:
        raise ValidationError({"team": ["Pick a team or name a new one, not both."]})
    if new_team_name is not None:
        return create_team(company, new_team_name)
    if team_id is None:
        return None
    team = Team.objects.filter(company=company, pk=team_id).first()
    if team is None:
        raise ValidationError({"team": ["Pick a team from your company."]})
    return team
