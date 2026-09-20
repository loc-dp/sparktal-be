from datetime import timedelta
from typing import Any

from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company
from apps.core.seed import SeedContext, register_resetter, register_seeder
from apps.jobs.functions import guess_function
from apps.jobs.models import BountyType, Job, JobStatus, PayBasis, WorkModel

# Mirrors prototypes/assets/data.js `jobs` (Vela Pay, employer side).
JOBS: list[dict[str, Any]] = [
    {
        "title": "Senior Backend Engineer",
        "days_ago": 38,
        "status": JobStatus.ACTIVE,
        "salary": (45_000_000, 60_000_000),
        "bounty_pct": 18,
        "work_model": WorkModel.HYBRID,
        "skills": ["Go", "PostgreSQL", "Kafka", "Kubernetes"],
        "why": "Payment volume doubled this year and the payout service needs a fourth owner.",
        "roles": "Own the payout service end to end\nDesign for 3x volume\nMentor two engineers",
        "quals": "5+ years with Go or Java\nStrong PostgreSQL\nHas run production systems on call",
    },
    {
        "title": "Data Analyst",
        "days_ago": 25,
        "status": JobStatus.ACTIVE,
        "salary": (25_000_000, 35_000_000),
        "bounty_pct": 15,
        "work_model": WorkModel.ONSITE,
        "skills": ["SQL", "dbt", "Looker"],
        "why": "Merchant teams make pricing calls weekly and need trustworthy numbers.",
        "roles": "Build the merchant cohort model\nOwn the weekly revenue pack",
        "quals": "3+ years in analytics\nExpert SQL\nComfortable with dbt",
    },
    {
        "title": "Product Designer",
        "days_ago": 17,
        "status": JobStatus.ACTIVE,
        "salary": (30_000_000, 42_000_000),
        "bounty_pct": 14,
        "work_model": WorkModel.REMOTE,
        "skills": ["Figma", "Design systems", "Prototyping"],
        "why": "The merchant dashboard is growing faster than its design system.",
        "roles": "Own the merchant dashboard\nGrow the design system",
        "quals": "4+ years designing B2B products\nStrong systems thinking",
    },
    {
        "title": "Compliance Manager",
        "days_ago": 10,
        "status": JobStatus.ACTIVE,
        "salary": (50_000_000, 70_000_000),
        "bounty_pct": 18,
        "work_model": WorkModel.ONSITE,
        "skills": ["AML", "SBV reporting", "KYC"],
        "why": "New State Bank rules land next quarter and we need an owner in-house.",
        "roles": "Own AML and KYC policy\nRun SBV reporting",
        "quals": "6+ years in payments or banking compliance\nFluent English",
    },
    {
        "title": "Head of Finance",
        "days_ago": 2,
        "status": JobStatus.PENDING,
        "salary": (90_000_000, 120_000_000),
        "bounty_pct": 20,
        "work_model": WorkModel.HYBRID,
        "skills": ["FP&A", "IFRS", "Treasury", "Fundraising", "Team leadership"],
        "why": "We are preparing for Series C and need one owner for FP&A and treasury.",
        "roles": "Own FP&A, budgeting and the board pack\nLead treasury across VND and USD",
        "quals": "10+ years in finance, 4+ leading a team\nIFRS and VAS\nHas run a fundraise",
    },
    {
        "title": "QA Engineer",
        "days_ago": 108,
        "status": JobStatus.CLOSED,
        "salary": (22_000_000, 30_000_000),
        "bounty_pct": 12,
        "work_model": WorkModel.ONSITE,
        "skills": ["Playwright", "API testing"],
        "why": "Release cadence went weekly and manual testing could not keep up.",
        "roles": "Own the regression suite\nAutomate the payout flows",
        "quals": "3+ years in QA automation\nPlaywright or Cypress",
    },
]

BENEFITS = "13th-month salary\nPremium health insurance\nLearning budget 20.000.000 ₫"


@register_seeder(order=40)
def seed(ctx: SeedContext) -> None:
    company: Company = ctx.refs.get("company") or Company.objects.get(name="Vela Pay")
    users: dict[str, User] = ctx.refs.get("users", {})
    creator = users.get("maianh.tran@velapay.vn") or User.objects.get(
        email="maianh.tran@velapay.vn"
    )
    offices = list(company.offices.all())
    now = timezone.now()

    jobs: dict[str, Job] = {}
    for spec in JOBS:
        created = now - timedelta(days=spec["days_ago"])
        office = None if spec["work_model"] == WorkModel.REMOTE else offices[0]
        job, _ = Job.objects.update_or_create(
            company=company,
            title=spec["title"],
            defaults={
                "created_by": creator,
                "function": guess_function(spec["title"], spec["skills"]),
                "why_we_need_you": spec["why"],
                "roles": spec["roles"],
                "qualifications": spec["quals"],
                "benefits": BENEFITS,
                "salary_min": spec["salary"][0],
                "salary_max": spec["salary"][1],
                "pay_basis": PayBasis.GROSS,
                "work_model": spec["work_model"],
                "office": office,
                "bounty_type": BountyType.PCT,
                "bounty_pct": spec["bounty_pct"],
                "skills": spec["skills"],
                "status": spec["status"],
                "published_at": None if spec["status"] == JobStatus.PENDING else created,
                "closed_at": now - timedelta(days=60)
                if spec["status"] == JobStatus.CLOSED
                else None,
            },
        )
        # `created_at` is auto_now_add, so backdate it with an update.
        Job.objects.filter(pk=job.pk).update(created_at=created)
        jobs[job.title] = job

    ctx.refs["jobs"] = jobs


@register_resetter(order=40)
def reset() -> None:
    """Drop jobs the demo company did not come with (E2E creates and renames some)."""
    Job.objects.filter(company__name="Vela Pay").exclude(
        title__in=[spec["title"] for spec in JOBS]
    ).delete()
