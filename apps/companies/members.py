from django.db import transaction
from rest_framework.exceptions import APIException, ValidationError

from apps.accounts.models import User, UserKind
from apps.companies.access import membership_of
from apps.companies.models import Company, CompanyMembership, CompanyRole, Team
from apps.jobs.models import PRIVATE_STATUSES, ChangeStatus, Job, JobChangeRequest

EMAIL_TAKEN = "This email already belongs to a Sparktal account."


class MemberRuleError(APIException):
    """A team rule was broken. Renders as 400 {"detail": "<sentence>"}."""

    status_code = 400
    default_code = "member_rule"


def admin_count(company: Company) -> int:
    return CompanyMembership.objects.filter(company=company, role=CompanyRole.ADMIN).count()


TEAM_REQUIRED = "Pick a team for a hiring manager or talent acquisition member."
TEAM_FOREIGN = "Pick a team from your company."
LAST_ADMIN = "A company needs at least one admin. Make someone else an admin first."


class _Unset:
    """Marks "team not given" so None can still mean "no team"."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()


@transaction.atomic
def invite_member(
    company: Company,
    *,
    email: str,
    full_name: str,
    role: str,
    team: Team | None = None,
    can_publish: bool = False,
) -> CompanyMembership:
    if role == CompanyRole.ADMIN:
        team, can_publish = None, False
    else:
        if team is None:
            raise ValidationError({"team": [TEAM_REQUIRED]})
        if team.company_id != company.pk:
            raise ValidationError({"team": [TEAM_FOREIGN]})
        can_publish = can_publish and role == CompanyRole.TALENT_ACQUISITION
    normalized = User.objects.normalize_email(email)
    existing = User.objects.filter(email__iexact=normalized).first()
    if existing is None:
        user = User.objects.create_user(
            email=normalized, password=None, full_name=full_name, kind=UserKind.COMPANY
        )
    elif (
        existing.kind == UserKind.COMPANY
        and not existing.is_active
        and membership_of(existing) is None
    ):
        user = existing
        user.full_name = full_name
        user.is_active = True
    else:
        raise ValidationError({"email": [EMAIL_TAKEN]})
    user.set_unusable_password()
    user.save()
    return CompanyMembership.objects.create(
        user=user, company=company, role=role, team=team, can_publish=can_publish
    )


@transaction.atomic
def update_member(
    membership: CompanyMembership,
    *,
    role: str | None = None,
    team: Team | None | _Unset = UNSET,  # noqa: RUF036
    can_publish: bool | None = None,
) -> CompanyMembership:
    old_role, old_team_id = membership.role, membership.team_id
    new_role = old_role if role is None else role
    if (
        old_role == CompanyRole.ADMIN
        and new_role != CompanyRole.ADMIN
        and admin_count(membership.company) <= 1
    ):
        raise MemberRuleError(LAST_ADMIN)

    new_team: Team | None
    if new_role == CompanyRole.ADMIN:
        new_team = None
    else:
        new_team = membership.team if isinstance(team, _Unset) else team
        if new_team is None:
            raise ValidationError({"team": [TEAM_REQUIRED]})
        if new_team.company_id != membership.company_id:
            raise ValidationError({"team": [TEAM_FOREIGN]})

    if new_role != CompanyRole.TALENT_ACQUISITION:
        new_can_publish = False
    elif can_publish is not None:
        new_can_publish = can_publish
    elif old_role != CompanyRole.TALENT_ACQUISITION:
        new_can_publish = False
    else:
        new_can_publish = membership.can_publish

    membership.role = new_role
    membership.team = new_team
    membership.can_publish = new_can_publish
    membership.save(update_fields=["role", "team", "can_publish"])
    if new_role != old_role or (new_team.pk if new_team else None) != old_team_id:
        withdraw_pending_proposals(membership.user)

    if new_team is not None and new_team.pk != old_team_id:
        # Private drafts follow their author; posted jobs stay with the old team.
        Job.objects.filter(
            created_by=membership.user, company=membership.company, status__in=PRIVATE_STATUSES
        ).update(team=new_team)
    return membership


def withdraw_pending_proposals(user: User) -> None:
    """A member who leaves, or moves team or role, no longer stands behind what they proposed."""
    JobChangeRequest.objects.filter(requested_by=user, status=ChangeStatus.PENDING).update(
        status=ChangeStatus.WITHDRAWN
    )


@transaction.atomic
def remove_member(membership: CompanyMembership, *, actor: User) -> None:
    if membership.user_id == actor.pk:
        raise MemberRuleError("You can't remove yourself. Ask another admin to do it.")
    if membership.role == CompanyRole.ADMIN and admin_count(membership.company) <= 1:
        raise MemberRuleError(LAST_ADMIN)
    user = membership.user
    withdraw_pending_proposals(user)
    membership.delete()
    user.is_active = False
    user.save(update_fields=["is_active"])
