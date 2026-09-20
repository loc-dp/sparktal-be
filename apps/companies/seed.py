from django.utils import timezone

from apps.accounts.models import User, UserKind
from apps.companies.models import (
    Company,
    CompanyMembership,
    CompanyRole,
    CompanyType,
    Industry,
    Location,
    Office,
    SizeRange,
)
from apps.core.seed import DEMO_PASSWORD, SeedContext, register_seeder

ABOUT = (
    "Vela Pay builds payment infrastructure for Southeast Asian merchants: QR checkout, "
    "instant payouts and reconciliation for more than 40,000 businesses in Vietnam. Our "
    "320-person team in Ho Chi Minh City and Ha Noi ships weekly, works hybrid (3 days in "
    "office) and every hire gets a learning budget of 20.000.000 ₫ a year."
)

MEMBERS = [
    ("nhung.le@velapay.vn", CompanyRole.ADMIN),
    ("maianh.tran@velapay.vn", CompanyRole.TALENT_ACQUISITION),
    ("khoa.nguyen@velapay.vn", CompanyRole.HIRING_MANAGER),
    ("huy.phan@velapay.vn", CompanyRole.HIRING_MANAGER),
]

OFFICES = [
    ("Bitexco Financial Tower, 2 Hải Triều, District 1, Ho Chi Minh City", Location.HCM),
    ("Capital Place, 29 Liễu Giai, Ba Đình, Ha Noi", Location.HANOI),
]


@register_seeder(order=20)
def seed(ctx: SeedContext) -> None:
    company, _ = Company.objects.update_or_create(
        name="Vela Pay",
        defaults={
            "industry": Industry.FINTECH,
            "company_type": CompanyType.FDI,
            "year_incorporated": 2019,
            "size_range": SizeRange.R100_500,
            "bd_headcount": 12,
            "located": Location.HCM,
            "about": ABOUT,
            "website": "https://velapay.vn",
            "linkedin": "https://www.linkedin.com/company/velapay",
            "facebook": "https://www.facebook.com/velapay.vn",
            "profile_completed_at": timezone.now(),
        },
    )
    company.offices.all().delete()
    Office.objects.bulk_create(
        Office(company=company, address=address, city=city, position=index)
        for index, (address, city) in enumerate(OFFICES)
    )

    users: dict[str, User] = ctx.refs.setdefault("users", {})
    for email, role in MEMBERS:
        user = users.get(email) or User.objects.get(email=email)
        CompanyMembership.objects.update_or_create(
            user=user, defaults={"company": company, "role": role}
        )

    # A second company whose profile is still empty: the onboarding path for E2E.
    onboarding, _ = Company.objects.update_or_create(
        name="Kestrel Logistics",
        defaults={
            "industry": "",
            "company_type": "",
            "year_incorporated": None,
            "size_range": "",
            "bd_headcount": None,
            "located": "",
            "about": "",
            "website": "",
            "linkedin": "",
            "facebook": "",
            "profile_completed_at": None,
        },
    )
    # Wipe anything an earlier run (or an E2E pass) left behind: this company is
    # the onboarding fixture and must always start empty.
    onboarding.offices.all().delete()
    new_admin, _ = User.objects.update_or_create(
        email="new.admin@example.com",
        defaults={"full_name": "Đỗ Thu Hà", "kind": UserKind.COMPANY, "is_active": True},
    )
    new_admin.set_password(DEMO_PASSWORD)
    new_admin.save(update_fields=["password"])
    CompanyMembership.objects.update_or_create(
        user=new_admin, defaults={"company": onboarding, "role": CompanyRole.ADMIN}
    )

    ctx.refs["company"] = company
    ctx.refs["onboarding_company"] = onboarding
