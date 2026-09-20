from typing import Any

# django-rules ships no stubs; its decorators erase types.
import rules

from apps.accounts.models import User
from apps.companies.access import membership_of
from apps.companies.models import Company, CompanyRole


@rules.predicate
def is_company_member(user: User, company: Company | None) -> bool:
    membership = membership_of(user)
    if membership is None:
        return False
    return company is None or membership.company_id == company.pk


def has_company_role(*roles: str) -> Any:
    @rules.predicate(name=f"has_company_role:{','.join(roles)}")
    def predicate(user: User, company: Company | None) -> bool:
        membership = membership_of(user)
        if membership is None:
            return False
        if company is not None and membership.company_id != company.pk:
            return False
        return membership.role in roles

    return predicate


is_company_admin = has_company_role(CompanyRole.ADMIN)
can_edit_company_profile = has_company_role(CompanyRole.ADMIN, CompanyRole.TALENT_ACQUISITION)

rules.add_perm("companies.view_company", is_company_member)
rules.add_perm("companies.change_company", can_edit_company_profile)
# Any member may create a job; per-job visibility is handled in apps/jobs (plan 04/05).
rules.add_perm("jobs.add_job", is_company_member)
