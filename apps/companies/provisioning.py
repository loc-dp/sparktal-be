"""Opening a company account: the company and its first admin (spec §10, "Công ty").

Sparktal does this, never the company itself: from the ops portal (`POST /ops/companies`) or
with `manage.py create_company`. Both send the admin's invite inside the same transaction, so an
email that cannot be sent leaves nothing behind.
"""

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User, UserKind
from apps.companies.members import EMAIL_TAKEN
from apps.companies.models import Company, CompanyMembership, CompanyRole

# Who the invite email says sent it when Sparktal opens the account or adds someone to it.
SPARKTAL_INVITER = "The Sparktal team"

COMPANY_NAME_MAX = 120
COMPANY_NAME_LENGTH = f"Give the company a name of 1 to {COMPANY_NAME_MAX} characters."


def clean_company_name(name: str) -> str:
    cleaned = name.strip()
    if not 1 <= len(cleaned) <= COMPANY_NAME_MAX:
        raise ValidationError({"name": [COMPANY_NAME_LENGTH]})
    return cleaned


@transaction.atomic
def provision_company(*, name: str, admin_email: str, admin_full_name: str) -> CompanyMembership:
    """Create the company, its admin's account and the ADMIN seat. Sends no email.

    The admin's account has no usable password: they set one from the invite the caller sends.
    An email any account already uses, active or not and of any kind, is refused.
    """
    company_name = clean_company_name(name)
    email = User.objects.normalize_email(admin_email.strip())
    taken = ValidationError({"admin_email": [EMAIL_TAKEN]})
    if User.objects.filter(email__iexact=email).exists():
        raise taken
    try:
        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                password=None,
                full_name=admin_full_name.strip(),
                kind=UserKind.COMPANY,
            )
    except IntegrityError as exc:  # lost a race for the same address
        raise taken from exc
    company = Company.objects.create(name=company_name)
    return CompanyMembership.objects.create(user=user, company=company, role=CompanyRole.ADMIN)
