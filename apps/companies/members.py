from django.db import transaction
from rest_framework.exceptions import APIException, ValidationError

from apps.accounts.models import User, UserKind
from apps.companies.access import membership_of
from apps.companies.models import Company, CompanyMembership, CompanyRole

EMAIL_TAKEN = "This email already belongs to a Sparktal account."


class MemberRuleError(APIException):
    """A team rule was broken. Renders as 400 {"detail": "<sentence>"}."""

    status_code = 400
    default_code = "member_rule"


def admin_count(company: Company) -> int:
    return CompanyMembership.objects.filter(company=company, role=CompanyRole.ADMIN).count()


@transaction.atomic
def invite_member(company: Company, *, email: str, full_name: str, role: str) -> CompanyMembership:
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
    return CompanyMembership.objects.create(user=user, company=company, role=role)


LAST_ADMIN = "A company needs at least one admin. Make someone else an admin first."


def change_role(membership: CompanyMembership, role: str) -> CompanyMembership:
    if (
        membership.role == CompanyRole.ADMIN
        and role != CompanyRole.ADMIN
        and admin_count(membership.company) <= 1
    ):
        raise MemberRuleError(LAST_ADMIN)
    membership.role = role
    membership.save(update_fields=["role"])
    return membership


@transaction.atomic
def remove_member(membership: CompanyMembership, *, actor: User) -> None:
    if membership.user_id == actor.pk:
        raise MemberRuleError("You can't remove yourself. Ask another admin to do it.")
    if membership.role == CompanyRole.ADMIN and admin_count(membership.company) <= 1:
        raise MemberRuleError(LAST_ADMIN)
    user = membership.user
    membership.delete()
    user.is_active = False
    user.save(update_fields=["is_active"])
