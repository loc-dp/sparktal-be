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
    Team,
)
from apps.core.seed import DEMO_PASSWORD, SeedContext, register_resetter, register_seeder
from apps.jobs.models import Job

ABOUT = (
    "Vela Pay builds payment infrastructure for Southeast Asian merchants: QR checkout, "
    "instant payouts and reconciliation for more than 40,000 businesses in Vietnam. Our "
    "320-person team in Ho Chi Minh City and Ha Noi ships weekly, works hybrid (3 days in "
    "office) and every hire gets a learning budget of 20.000.000 ₫ a year."
)

PAYMENTS = "Payments"
GROWTH = "Growth"
TEAMS = (PAYMENTS, GROWTH)

# email, role, team (admins have none), can_publish
MEMBERS: list[tuple[str, str, str | None, bool]] = [
    ("nhung.le@velapay.vn", CompanyRole.ADMIN, None, False),
    ("maianh.tran@velapay.vn", CompanyRole.TALENT_ACQUISITION, PAYMENTS, True),
    ("khoa.nguyen@velapay.vn", CompanyRole.HIRING_MANAGER, PAYMENTS, False),
    ("huy.phan@velapay.vn", CompanyRole.HIRING_MANAGER, GROWTH, False),
    ("thu.vo@velapay.vn", CompanyRole.TALENT_ACQUISITION, GROWTH, False),
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

    teams = {name: Team.objects.get_or_create(company=company, name=name)[0] for name in TEAMS}

    users: dict[str, User] = ctx.refs.setdefault("users", {})
    for email, role, team_name, can_publish in MEMBERS:
        user = users.get(email) or User.objects.get(email=email)
        CompanyMembership.objects.update_or_create(
            user=user,
            defaults={
                "company": company,
                "role": role,
                "team": teams[team_name] if team_name else None,
                "can_publish": can_publish,
            },
        )

    # Invited but has not set a password yet: the Team page shows "Invited".
    invited, _ = User.objects.update_or_create(
        email="tuan.vo@velapay.vn",
        defaults={
            "full_name": "Võ Anh Tuấn",
            "kind": UserKind.COMPANY,
            "is_active": True,
            "locked_at": None,
        },
    )
    invited.set_unusable_password()
    invited.save(update_fields=["password"])
    CompanyMembership.objects.update_or_create(
        user=invited,
        defaults={
            "company": company,
            "role": CompanyRole.TALENT_ACQUISITION,
            "team": teams[GROWTH],
            "can_publish": False,
        },
    )

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
    onboarding.offices.all().delete()
    new_admin, _ = User.objects.update_or_create(
        email="new.admin@example.com",
        defaults={
            "full_name": "Đỗ Thu Hà",
            "kind": UserKind.COMPANY,
            "is_active": True,
            "locked_at": None,
        },
    )
    new_admin.set_password(DEMO_PASSWORD)
    new_admin.save(update_fields=["password"])
    CompanyMembership.objects.update_or_create(
        user=new_admin, defaults={"company": onboarding, "role": CompanyRole.ADMIN}
    )

    ctx.refs["company"] = company
    ctx.refs["onboarding_company"] = onboarding


# What the E2E suite names the companies it opens through the ops portal: "E2E <digits>". Only
# that exact shape: a real customer called "E2E Logistics" on the demo server must survive.
E2E_COMPANY_NAME = r"^E2E \d+$"


@register_resetter(order=20)
def reset() -> None:
    """Teammates the E2E suite invited (and failed to clean up) are not part of the demo."""
    User.objects.filter(email__startswith="e2e.", email__endswith="@velapay.vn").delete()
    # A team is PROTECTed while it has members: drop the seats (the seeder recreates them),
    # then every team no job still sits in. Jobs-seeder leftovers are moved by the jobs seeder.
    CompanyMembership.objects.filter(company__name="Vela Pay").delete()
    Team.objects.filter(company__name="Vela Pay", jobs__isnull=True).delete()
    reset_e2e_companies()


def reset_e2e_companies() -> None:
    """Drop the companies the E2E suite opened, with everyone and everything in them.

    The recruiters resetter (order 30, so before this one) already deleted the
    `@e2e.example.com` users; this also takes anyone else who sat in such a company. Seats and
    jobs go before the company, because each of them PROTECTs its team.
    """
    companies = Company.objects.filter(name__regex=E2E_COMPANY_NAME)
    people = list(
        User.objects.filter(membership__company__in=companies).values_list("pk", flat=True)
    )
    CompanyMembership.objects.filter(company__in=companies).delete()
    # Every job, soft-deleted ones too; their applications, invites and assignments cascade.
    Job.objects.filter(company__in=companies).delete()
    companies.delete()
    User.objects.filter(pk__in=people).delete()
