from datetime import timedelta
from typing import Any

from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Company
from apps.core.seed import DEMO_EMAILS, SeedContext, register_resetter, register_seeder
from apps.jobs.functions import guess_function
from apps.jobs.models import (
    BountyType,
    Job,
    JobAssignment,
    JobStatus,
    Language,
    PayBasis,
    RoleType,
    SearchDuration,
    WorkingHours,
    WorkModel,
)
from apps.recruiters.models import RecruiterProfile

JOBS: list[dict[str, Any]] = [
    {
        "title": "Senior Backend Engineer",
        "days_ago": 38,
        "status": JobStatus.ACTIVE,
        "salary": (45_000_000, 60_000_000),
        "bounty_pct": 18,
        "work_model": WorkModel.HYBRID,
        "skills": ["Go", "PostgreSQL", "Kafka", "Kubernetes", "gRPC", "Redis"],
        "why": "Payment volume doubled to 5,000,000 transactions/month this year. The payout and settlement service needs a senior owner to design for 3x scale and guarantee strict idempotency.",
        "roles": "Own the ledger, payout and settlement services end to end\nArchitect high-throughput event-driven microservices in Go\nDesign automated double-entry ledger audits and reconciliation pipelines\nMentor mid-level backend engineers and lead incident post-mortems",
        "quals": "5+ years backend engineering experience with Go or Java\nProduction expertise in PostgreSQL query optimization and transaction isolation levels\nHands-on experience with Kafka event streaming and idempotency patterns\nHas run high-availability financial or payment production systems on call",
        "nice": "Experience with PCI DSS Level 1 or SBV technical audit standards\nContributions to open-source Go or distributed systems projects\nExperience with distributed consensus algorithms (Raft, etcd)",
        "benefits": "Premium healthcare PVI (full coverage for employee & dependents)\n20.000.000 ₫ annual learning and tech certification budget\nMacBook Pro M3 Max provided\nHybrid work model (3 days in Bitexco Financial Tower office, 2 days remote)",
        "brief": {
            "working_hours": WorkingHours.MON_FRI,
            "reporting_line": "Reports to the Engineering Manager, Payments. Works with 3 backend engineers and the SRE team; mentors 2 mid-level engineers.",
            "role_type": RoleType.NEW_POSITION,
            "has_internal_candidate": False,
            "search_duration": SearchDuration.OVER_1_MONTH,
            "interview_rounds_count": 3,
            "interview_process_details": "Round 1: 45-minute call with the Engineering Manager\nRound 2: 90-minute system design session with two senior engineers\nRound 3: 45-minute culture fit with the CTO",
            "languages": [Language.VIETNAMESE, Language.ENGLISH],
            "core_competencies": "Ownership of production systems\nRigour with money and data correctness\nCalm under incident pressure\nGenerous mentoring",
            "probation_expectations": "Ship the idempotent payout retry flow to production and lead one incident post-mortem within 60 days.",
            "salary_negotiable": True,
            "allowances": "13th-month salary + performance bonus (1–3 months)\nLunch allowance 1.000.000 ₫/month",
        },
        "note_for_recruiters": "",
    },
    {
        "title": "Data Analyst",
        "days_ago": 25,
        "status": JobStatus.ACTIVE,
        "salary": (25_000_000, 35_000_000),
        "bounty_pct": 15,
        "work_model": WorkModel.ONSITE,
        "skills": ["SQL", "dbt", "Looker", "Python", "BigQuery", "Tableau"],
        "why": "Merchant business teams make pricing and fee structure calls weekly. The data team needs a core analyst to build trustworthy revenue cohort models and merchant churn indicators.",
        "roles": "Build and maintain the merchant retention and cohort analytics model in dbt\nOwn the weekly executive revenue pack and transaction volume forecast\nPartner with product managers to design AB test metrics for QR merchant onboarding\nAutomate alerting on transaction fee anomalies and settlement delays",
        "quals": "3+ years in product or business analytics in fintech, e-commerce, or banking\nExpert SQL skills across analytical data warehouses (BigQuery/Snowflake/PostgreSQL)\nComfortable with dbt for modular, testable data modeling\nStrong business acumen and data storytelling ability in both Vietnamese and English",
        "nice": "Experience with financial reconciliation pipelines or payment merchant cohort modeling\nFamiliarity with Python (pandas, numpy) for statistical modeling\nLookML developer certification",
        "benefits": "Premium health insurance Bảo Việt\n15 days annual leave + 3 floating wellbeing days\nAnnual company retreat in Da Nang / Phu Quoc\n20.000.000 ₫ annual learning budget",
        "brief": {
            "working_hours": WorkingHours.MON_FRI,
            "reporting_line": "Reports to the Head of Data. Partners daily with Merchant Success and Product.",
            "role_type": RoleType.REPLACEMENT,
            "has_internal_candidate": False,
            "search_duration": SearchDuration.UNDER_1_MONTH,
            "interview_rounds_count": 3,
            "interview_process_details": "Round 1: 30-minute screen with the Head of Data\nRound 2: take-home SQL case, reviewed in a 60-minute call\nRound 3: 45-minute panel with Product and Finance",
            "languages": [Language.VIETNAMESE, Language.ENGLISH],
            "core_competencies": "Business curiosity\nClear storytelling with numbers\nAttention to detail",
            "probation_expectations": "Own the weekly executive revenue pack end to end by the end of month two.",
            "salary_negotiable": True,
            "allowances": "13th-month salary + quarterly performance bonus",
        },
        "note_for_recruiters": "Hiring manager: Nguyễn Minh Khoa. Please focus on candidates who have built automated SQL/dbt data marts, not just dashboards.",
    },
    {
        "title": "Product Designer",
        "days_ago": 17,
        "status": JobStatus.ACTIVE,
        "salary": (30_000_000, 42_000_000),
        "bounty_pct": 14,
        "work_model": WorkModel.REMOTE,
        "skills": ["Figma", "Design systems", "Prototyping", "User research", "Tailwind CSS"],
        "why": "Our merchant portal is expanding to multi-store management and POS hardware integrations. We need a product designer to evolve our design system and create friction-free merchant experiences.",
        "roles": "Own the end-to-end design lifecycle for the merchant portal and mobile companion app\nMaintain and scale the Sparktal Design System in Figma and code tokens\nConduct bi-weekly user research interviews with merchant store managers and cashiers\nCollaborate closely with engineers on UI component specifications and micro-interactions",
        "quals": "4+ years designing complex B2B SaaS or fintech web/mobile products\nStrong systems thinking with proven experience maintaining a production design system\nProficient in Figma, interactive prototyping, and usability testing methodologies\nFluent in Vietnamese and communicative in English",
        "nice": "Working knowledge of Tailwind CSS, React component props, and HTML semantic tags\nExperience designing for physical POS terminals or tablet interfaces\nMotion design skills using Principle or Rive",
        "benefits": "100% remote flexibility across Vietnam\nAnnual design conference budget and travel subsidy\nPremium private healthcare",
        "brief": {
            "working_hours": WorkingHours.MON_FRI,
            "reporting_line": "Reports to the Head of Product. Works with 2 product managers and 6 engineers.",
            "role_type": RoleType.NEW_POSITION,
            "has_internal_candidate": False,
            "search_duration": SearchDuration.UNDER_2_WEEKS,
            "interview_rounds_count": 2,
            "interview_process_details": "Round 1: 60-minute portfolio review with the Head of Product\nRound 2: 90-minute design critique with the product team",
            "languages": [Language.VIETNAMESE, Language.ENGLISH],
            "core_competencies": "Systems thinking\nEmpathy for merchants\nCraft in the details",
            "salary_negotiable": False,
            "allowances": "15.000.000 ₫ home office setup allowance",
        },
        "note_for_recruiters": "Portfolio required. We look for case studies demonstrating problem definition, wireframing iterations, and production metrics.",
    },
    {
        "title": "Compliance Manager",
        "days_ago": 10,
        "status": JobStatus.ACTIVE,
        "salary": (50_000_000, 70_000_000),
        "office": 1,
        "bounty_type": BountyType.FIXED,
        "bounty_amount": 120_000_000,
        "work_model": WorkModel.ONSITE,
        "skills": ["AML", "SBV reporting", "KYC", "Regulatory affairs", "Risk assessment"],
        "why": "State Bank of Vietnam (SBV) regulations on payment intermediaries and biometric verification are updating next quarter. We need an experienced compliance officer to lead regulatory reporting and transaction surveillance.",
        "roles": "Directly manage AML/CFT compliance policy, STR filing, and transaction monitoring rules\nOwn regulatory reporting submissions and quarterly audits with the State Bank of Vietnam\nReview merchant onboarding underwriting criteria and high-risk merchant classifications\nConduct regular staff compliance training and liaise with partner commercial banks",
        "quals": "6+ years in compliance, legal, or risk management within payments, fintech, or commercial banking\nIn-depth knowledge of Circular 39, Circular 40, and SBV payment intermediary frameworks\nDemonstrated experience managing direct SBV on-site inspections or external audit reviews\nFluent English and native Vietnamese",
        "nice": "ACAMS / CAMS (Certified Anti-Money Laundering Specialist) certification\nPrior legal degree or Bar association membership in Vietnam\nExperience in cross-border payment compliance frameworks",
        "benefits": "Competitive executive remuneration package\nAnnual executive medical checkup at FV Hospital\n20.000.000 ₫ professional development allowance",
        "brief": {
            "working_hours": WorkingHours.MON_SAT_HALF,
            "reporting_line": "Reports to the CEO, dotted line to the Chief Risk Officer. Manages 2 compliance officers.",
            "role_type": RoleType.REPLACEMENT,
            "has_internal_candidate": True,
            "search_duration": SearchDuration.UNDER_2_WEEKS,
            "interview_rounds_count": 4,
            "interview_process_details": "Round 1: HR screen\nRound 2: 60-minute interview with the Chief Risk Officer\nRound 3: case study on an SBV inspection\nRound 4: final interview with the CEO",
            "languages": [Language.VIETNAMESE, Language.ENGLISH],
            "core_competencies": "Integrity\nRegulatory judgement\nStakeholder management",
            "probation_expectations": "File the quarterly SBV report on time and close the open findings from the last audit.",
            "salary_negotiable": True,
            "allowances": "Dedicated parking space at Capital Place Hanoi office",
        },
        "note_for_recruiters": "Based in Hanoi office (Capital Place, 29 Liễu Giai). Candidates must have direct liaison experience with SBV Payment Department.",
    },
    {
        "title": "Head of Finance",
        "days_ago": 2,
        "status": JobStatus.PENDING,
        "salary": (90_000_000, 120_000_000),
        "bounty_pct": 20,
        "work_model": WorkModel.HYBRID,
        "skills": ["FP&A", "IFRS", "Treasury", "Fundraising", "Team leadership", "VAS"],
        "why": "Preparing for Series C international funding round and subsequent regional expansion. We require a seasoned finance leader to consolidate FP&A, treasury, and multi-currency capital allocation.",
        "roles": "Lead financial planning & analysis (FP&A), annual budgeting, and board reporting packs\nOversee corporate treasury operations across VND and USD settlement accounts\nLead finance due diligence workstreams with international venture capital and growth equity investors\nDirect the accounting department to ensure full compliance with both VAS and IFRS standards",
        "quals": "10+ years in corporate finance or investment banking, with 4+ years leading finance teams\nComprehensive mastery of both VAS and IFRS accounting standards\nProven track record leading or facilitating equity/debt fundraising rounds exceeding $15M\nFluent in business English with exceptional executive communication skills",
        "nice": "CPA, ACCA, or CFA charterholder\nPrior experience at Big 4 audit firm (Audit Manager level or above)\nExperience with FX hedging and multi-entity corporate structuring in SEA",
        "benefits": "Significant equity incentive (ESOP allocation from Series C pool)\nComprehensive global private health insurance for executive and family\nExecutive annual wellness and training budget\nHybrid working flexibility",
        "brief": {
            "working_hours": WorkingHours.MON_FRI,
            "reporting_line": "Reports directly to the CEO. Manages the accounting team (4) and the FP&A analyst.",
            "role_type": RoleType.NEW_POSITION,
            "has_internal_candidate": False,
            "search_duration": SearchDuration.JUST_CREATED,
            "interview_rounds_count": 4,
            "languages": [Language.VIETNAMESE, Language.ENGLISH],
            "core_competencies": "Strategic judgement\nInvestor-grade communication\nHigh integrity",
            "salary_negotiable": True,
        },
        "note_for_recruiters": "Confidential executive search. Reports directly to CEO. Please submit candidate teaser without contact info first.",
    },
    {
        "title": "QA Engineer",
        "days_ago": 108,
        "status": JobStatus.CLOSED,
        "salary": (22_000_000, 30_000_000),
        "bounty_pct": 12,
        "work_model": WorkModel.ONSITE,
        "skills": ["Playwright", "API testing", "Postman", "CI/CD", "TypeScript", "SQL"],
        "why": "Engineering moved to twice-weekly deployment cadence. We need a QA automation engineer to build reliable E2E regression suites for checkout, payment QR, and settlement webhooks.",
        "roles": "Build and expand the automated regression suite using Playwright and TypeScript\nAutomate API integration tests covering banking partner webhooks and idempotency\nEmbed automated test execution into GitHub Actions CI/CD pipelines\nCollaborate with developers on test strategy and edge-case scenario definition",
        "quals": "3+ years in QA automation for web applications and backend APIs\nStrong programming skills in TypeScript, JavaScript, or Python\nProduction experience with Playwright or Cypress\nSolid understanding of REST APIs, HTTP status codes, and relational database testing",
        "nice": "Performance and load testing experience with k6 or Locust\nFamiliarity with Docker and containerized test runners\nISTQB Advanced Test Automation certification",
        "benefits": "Premium health insurance Bảo Việt\nAnnual company retreat\n15 days annual leave",
        "brief": {
            "working_hours": WorkingHours.MON_FRI,
            "role_type": RoleType.NEW_POSITION,
            "has_internal_candidate": False,
            "search_duration": SearchDuration.OVER_3_MONTHS,
            "interview_rounds_count": 2,
            "languages": [Language.VIETNAMESE],
            "salary_negotiable": True,
            "allowances": "13th-month salary + yearly performance bonus",
        },
        "note_for_recruiters": "",
    },
]

BENEFITS = "13th-month salary\nPremium health insurance\nLearning budget 20.000.000 ₫"
BRIEF_TEXT_FIELDS = (
    "reporting_line",
    "interview_process_details",
    "core_competencies",
    "probation_expectations",
    "allowances",
)


def brief_defaults(spec: dict[str, Any], created: Any) -> dict[str, Any]:
    """Every brief answer, blank where the demo job gives none; the hire is due 60 days on."""
    brief = spec.get("brief", {})
    return {
        **{field: brief.get(field, "") for field in BRIEF_TEXT_FIELDS},
        "working_hours": brief.get("working_hours", ""),
        "role_type": brief.get("role_type", ""),
        "search_duration": brief.get("search_duration", ""),
        "has_internal_candidate": brief.get("has_internal_candidate"),
        "interview_rounds_count": brief.get("interview_rounds_count"),
        "languages": brief.get("languages", []),
        "salary_negotiable": brief.get("salary_negotiable"),
        "target_start_date": (created + timedelta(days=60)).date(),
    }


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
        office = None if spec["work_model"] == WorkModel.REMOTE else offices[spec.get("office", 0)]
        job, _ = Job.objects.update_or_create(
            company=company,
            title=spec["title"],
            defaults={
                "created_by": creator,
                "function": guess_function(spec["title"], spec["skills"]),
                "why_we_need_you": spec["why"],
                "roles": spec["roles"],
                "qualifications": spec["quals"],
                "nice_to_have": spec.get("nice", ""),
                "benefits": spec.get("benefits", BENEFITS),
                "salary_min": spec["salary"][0],
                "salary_max": spec["salary"][1],
                "pay_basis": PayBasis.GROSS,
                "work_model": spec["work_model"],
                "office": office,
                "bounty_type": spec.get("bounty_type", BountyType.PCT),
                "bounty_pct": spec.get("bounty_pct"),
                "bounty_amount": spec.get("bounty_amount"),
                "skills": spec["skills"],
                "status": spec["status"],
                "note_for_recruiters": spec.get("note_for_recruiters", ""),
                **brief_defaults(spec, created),
                "published_at": None if spec["status"] == JobStatus.PENDING else created,
                "closed_at": now - timedelta(days=60)
                if spec["status"] == JobStatus.CLOSED
                else None,
            },
        )
        Job.objects.filter(pk=job.pk).update(created_at=created)
        jobs[job.title] = job

    ctx.refs["jobs"] = jobs


@register_resetter(order=40)
def reset() -> None:
    """Drop jobs the demo company did not come with (E2E creates and renames some)."""
    Job.objects.filter(company__name="Vela Pay").exclude(
        title__in=[spec["title"] for spec in JOBS]
    ).delete()


ASSIGNMENTS: dict[str, list[str]] = {
    "NorthStar Talent": [
        "Senior Backend Engineer",
        "Data Analyst",
        "Compliance Manager",
        "QA Engineer",
    ],
    "Bluefin Search": ["Senior Backend Engineer", "Product Designer", "Compliance Manager"],
}


@register_seeder(order=45)
def seed_assignments(ctx: SeedContext) -> None:
    jobs: dict[str, Job] = ctx.refs.get("jobs") or {
        job.title: job for job in Job.objects.filter(company__name="Vela Pay")
    }
    recruiters: dict[str, RecruiterProfile] = ctx.refs["recruiters"]
    created: list[JobAssignment] = []
    for nickname, titles in ASSIGNMENTS.items():
        recruiter = recruiters[nickname]
        for title in titles:
            assignment, _ = JobAssignment.objects.get_or_create(
                job=jobs[title], recruiter=recruiter
            )
            if assignment.left_at is not None:
                assignment.left_at = None
                assignment.save(update_fields=["left_at"])
            # `joined_at` is auto_now_add: join a few days after the job went live.
            published = jobs[title].published_at or timezone.now()
            JobAssignment.objects.filter(pk=assignment.pk).update(
                joined_at=min(published + timedelta(days=2), timezone.now())
            )
            created.append(assignment)
    ctx.refs["assignments"] = created


@register_resetter(order=45)
def reset_assignments() -> None:
    JobAssignment.objects.filter(recruiter__user__email__in=DEMO_EMAILS).delete()
