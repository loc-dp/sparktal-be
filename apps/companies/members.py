from functools import partial

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from apps.accounts.models import User, UserKind
from apps.accounts.sessions import end_sessions
from apps.chat.events import close_sockets
from apps.companies.access import membership_of
from apps.companies.models import Company, CompanyMembership, CompanyRole, Team
from apps.jobs.models import PRIVATE_STATUSES, ChangeStatus, Job, JobChangeRequest

EMAIL_TAKEN = "This email already belongs to a Sparktal account."
ACCOUNT_LOCKED = "Sparktal has locked this account."


class MemberRuleError(APIException):
    """A team rule was broken. Renders as 400 {"detail": "<sentence>"}."""

    status_code = 400
    default_code = "member_rule"


def is_last_admin(membership: CompanyMembership) -> bool:
    """Whether the company would be left with no admin it can use without this admin seat.

    An admin Sparktal locked cannot manage the company, so they are no successor to an unlocked
    admin. A locked admin going leaves the usable admins as they were, so any other admin seat is
    enough then. Sparktal locking the last admin is not guarded: that is its own decision.

    Run it in the transaction that then changes the seat. It locks the company's admin seats
    until commit, always in pk order and before anything else is locked, so two admins stepping
    down at once wait their turn instead of both passing, and cannot deadlock on each other.
    """
    admin_seats = CompanyMembership.objects.filter(
        company_id=membership.company_id, role=CompanyRole.ADMIN
    )
    list(admin_seats.select_for_update().order_by("pk").values_list("pk", flat=True))
    # Read after the wait: a seat another admin's change just committed is seen as it is now.
    others = admin_seats.exclude(pk=membership.pk)
    if membership.user.locked_at is None:
        others = others.filter(user__locked_at__isnull=True)
    return not others.exists()


TEAM_REQUIRED = "Pick a team for a hiring manager or talent acquisition member."
TEAM_FOREIGN = "Pick a team from your company."
LAST_ADMIN = "A company needs at least one admin. Make someone else an admin first."
LOCKED_NO_INVITE = "This account is locked. Unlock it before sending the invite again."


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
    locked_detail: str = EMAIL_TAKEN,
) -> CompanyMembership:
    """Seat `email` in `company`: a new account, or one removed from a company before.

    `locked_detail` is what the caller is told when the address belongs to a removed member
    Sparktal locked. A company must not learn of a Sparktal lock, so the default calls the
    address taken; Sparktal's own portal passes `ACCOUNT_LOCKED`.
    """
    if role == CompanyRole.ADMIN:
        team, can_publish = None, False
    else:
        if team is None:
            raise ValidationError({"team": [TEAM_REQUIRED]})
        if team.company_id != company.pk:
            raise ValidationError({"team": [TEAM_FOREIGN]})
        can_publish = can_publish and role == CompanyRole.TALENT_ACQUISITION
    normalized = User.objects.normalize_email(email)
    # Locked until commit: the full save below must not overwrite a newer row, and two
    # companies inviting the same removed member at once must not both seat them.
    existing = User.objects.select_for_update().filter(email__iexact=normalized).first()
    if existing is None:
        try:
            # A savepoint: losing the race below must leave the caller's transaction usable.
            with transaction.atomic():
                user = User.objects.create_user(
                    email=normalized, password=None, full_name=full_name, kind=UserKind.COMPANY
                )
        except IntegrityError as exc:
            # Another invite made the account after we looked. It is brand new, not locked, so
            # `locked_detail` does not apply.
            raise ValidationError({"email": [EMAIL_TAKEN]}) from exc
    elif (
        existing.kind == UserKind.COMPANY
        and not existing.is_active
        and membership_of(existing) is None
    ):
        # Someone removed from a company. Removing a member Sparktal locked and inviting the
        # address again, here or in another company, must not undo the lock. (A locked member
        # still in a seat gets EMAIL_TAKEN below, so no other company learns of the lock.)
        if existing.locked_at is not None:
            raise ValidationError({"email": [locked_detail]})
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
        and is_last_admin(membership)
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
    if membership.role == CompanyRole.ADMIN and is_last_admin(membership):
        raise MemberRuleError(LAST_ADMIN)
    user = membership.user
    withdraw_pending_proposals(user)
    membership.delete()
    user.is_active = False
    user.save(update_fields=["is_active"])


@transaction.atomic
def set_locked(membership: CompanyMembership, *, locked: bool) -> CompanyMembership:
    """Suspend or restore a member's account. Only Sparktal does this (spec §10).

    Locking switches the account off, marks it locked and signs it out everywhere (open chat
    sockets close once the lock is saved), and nothing else: the seat, its team and the jobs
    stay as they are. The user is anonymous from their next request and cannot sign in. Nothing
    but Sparktal's unlock switches the account back on: not removing the seat and inviting the
    address again, nor the admin's "Active" box (the database refuses it). Unlocking lets them
    sign in again; a session that was open when they were locked does not come back. The last
    admin may be locked too: Sparktal may suspend a whole company.
    """
    user = membership.user
    if locked:
        user.locked_at = user.locked_at or timezone.now()
        user.is_active = False
    else:
        user.locked_at = None
        user.is_active = True
    user.save(update_fields=["is_active", "locked_at"])
    if locked:
        end_sessions(user)
        # Best effort: the lock stands even if the channel layer is down.
        transaction.on_commit(partial(close_sockets, user), robust=True)
    return membership


def unlock_users(users: QuerySet[User]) -> tuple[int, int]:
    """Sparktal's unlock for any user, seated or not; returns (switched back on, left off).

    A user still in a seat is switched back on, as the ops unlock does. One removed while
    locked stays switched off, unlocked: any company may then invite them again.
    """
    locked = users.filter(locked_at__isnull=False)
    with transaction.atomic():
        restored = locked.filter(membership__isnull=False).update(locked_at=None, is_active=True)
        left_off = locked.filter(membership__isnull=True).update(locked_at=None)
    return restored, left_off
