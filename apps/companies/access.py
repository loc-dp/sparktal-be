from apps.accounts.models import User
from apps.companies.models import Company, CompanyMembership


def membership_of(user: User | None) -> CompanyMembership | None:
    """The user's company membership, or None for recruiters, staff and anonymous users."""
    if user is None or not user.is_authenticated:
        return None
    membership: CompanyMembership | None = getattr(user, "membership", None)
    return membership


def company_of(user: User | None) -> Company | None:
    membership = membership_of(user)
    return membership.company if membership else None
