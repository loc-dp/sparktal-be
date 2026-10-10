import hashlib
import unicodedata
from datetime import date, timedelta
from typing import Any

from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.text import slugify

from apps.candidates.duplicates import find_duplicate
from apps.candidates.models import Application, Candidate, Stage
from apps.companies.models import Location
from apps.core.seed import DEMO_EMAILS, SeedContext, register_resetter, register_seeder
from apps.core.testing.pdf import make_pdf
from apps.jobs.models import Job
from apps.recruiters.models import RecruiterProfile

BACKEND: list[tuple[str, str, str, str, int, bool]] = [
    ("Phạm Minh Quân", "Backend Engineer · Tiki", "NorthStar Talent", Stage.INTERVIEW, 22, False),
    ("Nguyễn Hoàng Anh", "Senior Go Engineer · MoMo", "NorthStar Talent", Stage.OFFER, 30, False),
    ("Lê Thu Trang", "Software Engineer · Grab", "NorthStar Talent", Stage.SCREEN, 4, False),
    ("Hoàng Bảo Ngọc", "Backend Lead · VNG", "Bluefin Search", Stage.NEW, 0, False),
    ("Trịnh Quốc Việt", "Java Engineer · FPT Software", "Bluefin Search", Stage.SCREEN, 9, False),
    ("Đặng Khánh Vy", "Platform Engineer · Shopee", "Bluefin Search", Stage.INTERVIEW, 18, False),
    ("Bùi Anh Tuấn", "Senior Engineer · Axon Active", "Atlas Hunters", Stage.NEW, 1, False),
    ("Vũ Thảo Nguyên", "Backend Engineer · KMS", "Atlas Hunters", Stage.SHORTLISTED, 3, False),
    ("Mai Gia Bảo", "Node.js Engineer · Haravan", "NorthStar Talent", Stage.SHORTLISTED, 2, False),
    ("Tô Minh Châu", "SRE · Zalo", "Atlas Hunters", Stage.INTERVIEW, 14, False),
    ("Vũ Khánh Linh", "Backend Engineer · Base.vn", "Bluefin Search", Stage.REJECTED, 28, False),
]

OTHER: list[tuple[str, str, str, str, str, int]] = [
    ("Data Analyst", "Nguyễn Thanh Hằng", "Analyst · Tiki", "NorthStar Talent", Stage.SCREEN, 12),
    ("Data Analyst", "Trần Đức Long", "BI Engineer · MoMo", "Atlas Hunters", Stage.NEW, 3),
    ("Product Designer", "Lý Hoàng Yến", "Designer · MoMo", "Bluefin Search", Stage.SCREEN, 8),
    ("QA Engineer", "Phan Nhật Minh", "QA Lead · VNG", "NorthStar Talent", Stage.ONBOARDED, 90),
]

REJECTION_REASON = (
    "Strong engineer, but no payments domain experience and the salary ask is above range."
)

DUPLICATE_TWIN = ("Phạm Minh Quân", "Backend Engineer · Tiki", "Bluefin Search", 3)

ASSESSMENT = {
    "reason_for_leaving": "Flat ladder: no staff-level track opened in two years.",
    "motivation": "Wants payment-scale traffic and a lead who reviews design docs.",
    "other_notes": "30-day notice period. Asks about family health insurance.",
    "consultant_note": "Thinks in trade-offs and pushes back on vague requirements with data.",
}

CANDIDATE_ASSESSMENTS: dict[str, dict[str, str]] = {
    "Phạm Minh Quân": {
        "reason_for_leaving": "Tiki froze promotions and salary reviews following the 2025 reorg. Acted as de-facto lead for the checkout order domain for 14 months without compensation adjustment.",
        "motivation": "Targeting high-throughput fintech platforms, specifically double-entry ledger design and settlement services. Vela Pay's hybrid model (3 office days at Bitexco) matches his preference.",
        "other_notes": "Salary ask: 60.000.000 ₫ gross/month. 30-day notice period. Requests family health insurance for spouse and child.",
        "consultant_note": "Exceptional clarity when discussing system trade-offs. Defends architectural choices with metrics and production failure retrospectives. Strongly recommended.",
    },
    "Nguyễn Hoàng Anh": {
        "reason_for_leaving": "Spent 3 years maintaining consumer wallet frontend APIs at MoMo; eager to transition to core transactional settlement and banking integration layer.",
        "motivation": "Drawn to Vela Pay's Series C roadmap and autonomy given to senior engineers. Wants to lead high-concurrency Go services handling 10k+ TPS.",
        "other_notes": "Salary ask: 58.000.000 ₫ gross/month. Offer accepted. Ready to start 15/10/2026. Signed offer letter pending HR onboarding formalities.",
        "consultant_note": "Ranked in the top 5% of Go engineers in HCMC. Authored high-throughput message brokers in Go. Fast problem solver with strong distributed systems theory.",
    },
    "Lê Thu Trang": {
        "reason_for_leaving": "Seeking better work-life balance and a hybrid workplace closer to District 1 after 2 years on Grab's transport dispatch team.",
        "motivation": "Keen on merchant payout workflows and eager to work closely with Vietnamese financial institutions. Appreciates Vela Pay's modern Go/Postgres stack.",
        "other_notes": "Salary ask: 48.000.000 ₫ gross/month. Available within 2 weeks. Open to occasional on-call shifts.",
        "consultant_note": "Graduated valedictorian in Computer Science from HCMUT. Excellent analytical clarity and structured communication in both Vietnamese and English (IELTS 7.5).",
    },
    "Hoàng Bảo Ngọc": {
        "reason_for_leaving": "Looking for fresh technical challenges outside gaming infrastructure. Wants to apply high-load distributed caching expertise to payment routing.",
        "motivation": "Wants exposure to financial audit standards and automated reconciliation pipelines at scale.",
        "other_notes": "Salary ask: 62.000.000 ₫ gross/month. Notice period: 45 days. Candidate duplicate submission flagged in candidate radar.",
        "consultant_note": "Heavyweight engineering lead with 7 years managing large-scale C++ and Go server clusters at VNG. Very disciplined engineering methodology.",
    },
    "Trịnh Quốc Việt": {
        "reason_for_leaving": "Wants to transition permanently from enterprise outsourcing delivery at FPT Software to an in-house product company with direct customer ownership.",
        "motivation": "Passionate about transitioning primary focus from Java/Spring Boot to modern Go microservices and event-driven architectures.",
        "other_notes": "Salary ask: 42.000.000 ₫ gross/month. 30 days notice period. Open to technical screening tests.",
        "consultant_note": "Extremely diligent and humble engineer. Solid core CS fundamentals, understands SQL indexing and ACID properties deeply.",
    },
    "Đặng Khánh Vy": {
        "reason_for_leaving": "Extreme on-call burnout during mega-sale campaigns (11.11, 12.12) at Shopee. Seeks a mature engineering environment with sustainable operational practices.",
        "motivation": "Excited by Vela Pay's microservice container orchestration on Kubernetes and well-structured SRE practices.",
        "other_notes": "Salary ask: 55.000.000 ₫ gross/month. Notice period 30 days. Round 1 technical interview received unanimous positive feedback.",
        "consultant_note": "Exceptional Kubernetes, Docker, and Kafka partitions expertise. Strong debugging instincts under pressure.",
    },
    "Bùi Anh Tuấn": {
        "reason_for_leaving": "Completed Swiss banking client delivery milestone at Axon Active. Ready for full-time product role with equity upside.",
        "motivation": "Attracted by fast-moving domestic payment volume and the prospect of building core payment gateway modules.",
        "other_notes": "Salary ask: 46.000.000 ₫ gross/month. 30 days notice. Fluent in English.",
        "consultant_note": "Highly disciplined Scrum practitioner. Writes clean, self-documenting code with comprehensive unit test coverage.",
    },
    "Vũ Thảo Nguyên": {
        "reason_for_leaving": "Seeking career progression into payment ledger domains after 3 consecutive years on SaaS backend projects at KMS.",
        "motivation": "Passionate about transactional data integrity, database concurrency control, and zero-downtime database migrations.",
        "other_notes": "Salary ask: 44.000.000 ₫ gross/month. Available in 30 days.",
        "consultant_note": "Solid backend profile with strong PostgreSQL optimization skills and good English proficiency.",
    },
    "Mai Gia Bảo": {
        "reason_for_leaving": "Haravan backend is predominantly Node.js/TypeScript. He is determined to transition to Go as his primary production language.",
        "motivation": "Has built checkout services and order synchronization; eager to bring merchant checkout experience into Vela Pay's QR infrastructure.",
        "other_notes": "Salary ask: 38.000.000 ₫ gross/month. Short notice period (15 days remaining).",
        "consultant_note": "High enthusiasm, fast learner. Has already built sample payment webhook services in Go in his spare time.",
    },
    "Tô Minh Châu": {
        "reason_for_leaving": "Seeking compensation progression and broader multi-cloud infrastructure ownership beyond Zalo's private cloud.",
        "motivation": "Eager to design hybrid cloud disaster recovery and multi-region failover for payment processing.",
        "other_notes": "Salary ask: 56.000.000 ₫ gross/month. Round 2 interview scheduled.",
        "consultant_note": "Deep SRE background. Knows Linux internals, eBPF, Prometheus monitoring, and Kafka cluster tuning thoroughly.",
    },
    "Vũ Khánh Linh": {
        "reason_for_leaving": "Completed tenure at Base.vn, seeking senior engineering role in fintech.",
        "motivation": "Wants exposure to high transaction volumes and payment protocols.",
        "other_notes": "Salary ask: 65.000.000 ₫ gross/month (exceeded team budget for this band).",
        "consultant_note": "Solid generalist engineer, but lacks payment domain knowledge and salary expectations exceeded approved compensation band.",
    },
    "Nguyễn Thanh Hằng": {
        "reason_for_leaving": "Limited autonomy in current data analyst position at Tiki. Wants to directly influence merchant pricing strategy.",
        "motivation": "Excited to own merchant cohort analytics and transaction pricing intelligence from day one.",
        "other_notes": "Salary ask: 32.000.000 ₫ gross/month. Notice period: 30 days.",
        "consultant_note": "Brilliant SQL and dbt practitioner. Communicates data insights clearly to non-technical stakeholders.",
    },
    "Trần Đức Long": {
        "reason_for_leaving": "Seeking closer alignment with commercial merchant teams after 2 years in central BI at MoMo.",
        "motivation": "Wants to build real-time transaction reporting and anomaly detection dashboards for merchants.",
        "other_notes": "Salary ask: 34.000.000 ₫ gross/month. New applicant awaiting CV screening.",
        "consultant_note": "Strong experience with Looker, PostgreSQL, and merchant analytics metrics. Good portfolio.",
    },
    "Lý Hoàng Yến": {
        "reason_for_leaving": "Desires broader scope across web design systems, having focused solely on mini-apps at MoMo.",
        "motivation": "Enthusiastic about shaping the merchant portal design system and conducting merchant store visits.",
        "other_notes": "Salary ask: 38.000.000 ₫ gross/month. Screen interview scheduled.",
        "consultant_note": "Very strong Figma portfolio with thorough component token documentation and clean auto-layouts.",
    },
    "Phan Nhật Minh": {
        "reason_for_leaving": "Seeking product company with modern Playwright automation after leading testing at VNG.",
        "motivation": "Passionate about full-coverage CI/CD test automation for payment checkout pipelines.",
        "other_notes": "Successfully onboarded at 28.000.000 ₫ gross/month. Guarantee period active.",
        "consultant_note": "Top-tier QA automation engineer. Delivered comprehensive test framework within first month.",
    },
}

CANDIDATE_META: dict[str, dict[str, Any]] = {
    "Phạm Minh Quân": {
        "skills": ["Go", "PostgreSQL", "Kafka", "Redis"],
        "years": 6,
        "curr_sal": 45_000_000,
        "exp_sal": 60_000_000,
        "loc": Location.HCM,
    },
    "Nguyễn Hoàng Anh": {
        "skills": ["Go", "gRPC", "PostgreSQL", "Kafka"],
        "years": 7,
        "curr_sal": 48_000_000,
        "exp_sal": 58_000_000,
        "loc": Location.HCM,
    },
    "Lê Thu Trang": {
        "skills": ["Go", "Python", "Microservices", "Docker"],
        "years": 4,
        "curr_sal": 38_000_000,
        "exp_sal": 48_000_000,
        "loc": Location.HCM,
    },
    "Hoàng Bảo Ngọc": {
        "skills": ["Go", "C++", "Distributed Systems", "Kafka"],
        "years": 8,
        "curr_sal": 52_000_000,
        "exp_sal": 62_000_000,
        "loc": Location.HCM,
    },
    "Trịnh Quốc Việt": {
        "skills": ["Java", "Spring Boot", "PostgreSQL", "Docker"],
        "years": 6,
        "curr_sal": 34_000_000,
        "exp_sal": 42_000_000,
        "loc": Location.HCM,
    },
    "Đặng Khánh Vy": {
        "skills": ["Kubernetes", "Kafka", "Docker", "Go", "Terraform"],
        "years": 5,
        "curr_sal": 45_000_000,
        "exp_sal": 55_000_000,
        "loc": Location.HCM,
    },
    "Bùi Anh Tuấn": {
        "skills": ["Java", "Go", "Spring", "AWS", "CI/CD"],
        "years": 5,
        "curr_sal": 38_000_000,
        "exp_sal": 46_000_000,
        "loc": Location.HCM,
    },
    "Vũ Thảo Nguyên": {
        "skills": ["PostgreSQL", "Node.js", "Go", "Kafka"],
        "years": 4,
        "curr_sal": 35_000_000,
        "exp_sal": 44_000_000,
        "loc": Location.HCM,
    },
    "Mai Gia Bảo": {
        "skills": ["Node.js", "TypeScript", "Go", "Redis"],
        "years": 4,
        "curr_sal": 30_000_000,
        "exp_sal": 38_000_000,
        "loc": Location.HCM,
    },
    "Tô Minh Châu": {
        "skills": ["Linux", "Kubernetes", "Prometheus", "Kafka", "eBPF"],
        "years": 6,
        "curr_sal": 45_000_000,
        "exp_sal": 56_000_000,
        "loc": Location.HCM,
    },
    "Vũ Khánh Linh": {
        "skills": ["Python", "Django", "PostgreSQL", "Docker"],
        "years": 5,
        "curr_sal": 42_000_000,
        "exp_sal": 65_000_000,
        "loc": Location.HANOI,
    },
    "Nguyễn Thanh Hằng": {
        "skills": ["SQL", "dbt", "Tableau", "Looker", "BigQuery"],
        "years": 3,
        "curr_sal": 25_000_000,
        "exp_sal": 32_000_000,
        "loc": Location.HCM,
    },
    "Trần Đức Long": {
        "skills": ["SQL", "Looker", "PostgreSQL", "Python"],
        "years": 3,
        "curr_sal": 26_000_000,
        "exp_sal": 34_000_000,
        "loc": Location.HCM,
    },
    "Lý Hoàng Yến": {
        "skills": ["Figma", "Design systems", "Prototyping", "User Research"],
        "years": 4,
        "curr_sal": 30_000_000,
        "exp_sal": 38_000_000,
        "loc": Location.HCM,
    },
    "Phan Nhật Minh": {
        "skills": ["Playwright", "Cypress", "TypeScript", "Postman"],
        "years": 5,
        "curr_sal": 22_000_000,
        "exp_sal": 28_000_000,
        "loc": Location.HCM,
    },
}


def demo_identity(name: str) -> tuple[str, str, date]:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    email = f"{digest[:10]}@example.com"
    phone = f"+849{int(digest[10:18], 16) % 100_000_000:08d}"
    dob = date(1985, 1, 1) + timedelta(days=int(digest[18:24], 16) % 5_000)
    return email, phone, dob


POOL_ONLY: list[dict[str, Any]] = [
    {
        "nickname": "NorthStar Talent",
        "full_name": "Đỗ Hải Yến",
        "current_title": "Frontend Engineer · Shopee",
        "skills": ["React", "TypeScript", "Next.js", "Tailwind CSS"],
        "years_experience": 6,
        "dob": date(1994, 3, 8),
        "expected_salary": 45_000_000,
        "current_salary": 38_000_000,
        "location": Location.HCM,
    },
    {
        "nickname": "NorthStar Talent",
        "full_name": "Ngô Tuấn Kiệt",
        "current_title": "Fullstack Developer · MoMo",
        "skills": ["React", "Node.js", "GraphQL", "PostgreSQL"],
        "years_experience": 5,
        "dob": date(1995, 11, 22),
        "expected_salary": 42_000_000,
        "current_salary": 35_000_000,
        "location": Location.HCM,
    },
    {
        "nickname": "NorthStar Talent",
        "full_name": "Lương Bảo Trâm",
        "current_title": "Junior Frontend Developer · Sapo",
        "skills": ["React", "Redux", "JavaScript"],
        "years_experience": 1,
        "dob": date(2001, 5, 2),
        "expected_salary": 18_000_000,
        "current_salary": 14_000_000,
        "location": Location.HANOI,
    },
    {
        "nickname": "NorthStar Talent",
        "full_name": "Hồ Quốc Duy",
        "current_title": "Backend Engineer · Zalo",
        "skills": ["Go", "Kafka", "PostgreSQL", "Docker"],
        "years_experience": 8,
        "dob": date(1992, 1, 17),
        "expected_salary": 58_000_000,
        "current_salary": 48_000_000,
        "location": Location.HCM,
    },
    {
        "nickname": "Bluefin Search",
        "full_name": "Nguyễn Kim Chi",
        "current_title": "Frontend Engineer · VNG",
        "skills": ["React", "Vue", "CSS", "TypeScript"],
        "years_experience": 4,
        "dob": date(1996, 9, 30),
        "expected_salary": 36_000_000,
        "current_salary": 30_000_000,
        "location": Location.HCM,
    },
    {
        "nickname": "Bluefin Search",
        "full_name": "Marco Alvarez",
        "current_title": "Fullstack Engineer · Grab",
        "skills": ["React", "Python", "AWS", "FastAPI"],
        "years_experience": 9,
        "dob": date(1989, 7, 12),
        "expected_salary": 85_000_000,
        "current_salary": 75_000_000,
        "is_expat": True,
        "languages": ["English", "Spanish"],
        "location": Location.HCM,
    },
]

POOL_NOTE = "Sourced from the {title} community. Open to a move within the next quarter."


def _pool_candidate(row: dict[str, Any], recruiter: RecruiterProfile) -> Candidate:
    slug = slugify(
        unicodedata.normalize("NFKD", row["full_name"]).encode("ascii", "ignore").decode()
    )
    stable = int(hashlib.sha256(row["full_name"].encode("utf-8")).hexdigest()[:8], 16)
    candidate, _ = Candidate.objects.update_or_create(
        full_name=row["full_name"],
        owner=recruiter,
        defaults={
            "current_title": row["current_title"],
            "email": f"{slug}@example.com",
            "phone": f"+8493{stable % 10_000_000:07d}",
            "dob": row["dob"],
            "location": Location.HCM,
            "is_expat": row.get("is_expat", False),
            "languages": row.get("languages", ["Vietnamese", "English"]),
            "skills": row["skills"],
            "years_experience": row["years_experience"],
            "expected_salary": row["expected_salary"],
            "notes": POOL_NOTE.format(title=row["current_title"].split(" · ")[0]),
        },
    )
    if not candidate.cv_file:
        candidate.cv_file.save(
            "cv.pdf",
            ContentFile(
                make_pdf(
                    f"{row['full_name']}\n{row['current_title']}\n\n"
                    f"Skills\n{', '.join(row['skills'])}\n\n"
                    f"Summary\n{row['years_experience']} years of experience."
                )
            ),
            save=False,
        )
        candidate.cv_filename = f"{slug}-cv.pdf"
        candidate.save()
    return candidate


def _application(
    job: Job,
    name: str,
    title: str,
    recruiter: RecruiterProfile,
    stage: str,
    days_ago: int,
    duplicate: bool,
) -> Application:
    email, phone, dob = demo_identity(name)
    meta = CANDIDATE_META.get(name, {})
    candidate, _ = Candidate.objects.update_or_create(
        full_name=name,
        owner=recruiter,
        defaults={
            "current_title": title,
            "email": email,
            "phone": phone,
            "dob": dob,
            "location": meta.get("loc", Location.HCM),
            "skills": meta.get("skills", ["Go", "PostgreSQL", "Kafka"]),
            "years_experience": meta.get("years", 5),
            "current_salary": meta.get("curr_sal", 35_000_000),
            "expected_salary": meta.get("exp_sal", 50_000_000),
            "languages": ["Vietnamese", "English"],
            "notes": f"Candidate profile verified by {recruiter.nickname}. Sourced from {title.split(' · ')[-1]}.",
        },
    )
    submitted = timezone.now() - timedelta(days=days_ago)
    assessment = CANDIDATE_ASSESSMENTS.get(name, ASSESSMENT)
    application, created = Application.objects.update_or_create(
        job=job,
        candidate=candidate,
        defaults={
            "recruiter": recruiter,
            "stage": stage,
            "cv_filename": f"{name} - CV.pdf",
            "submitted_at": submitted,
            "stage_changed_at": submitted + timedelta(days=1),
            "is_duplicate": duplicate,
            "rejected_reason": REJECTION_REASON if stage == Stage.REJECTED else "",
            **assessment,
        },
    )
    if created or not application.cv_file:
        company_name = title.split(" · ")[-1] if " · " in title else "Tech Company"
        role_name = title.split(" · ")[0] if " · " in title else title
        cv_text = (
            f"{name}\n{title}\n\n"
            f"PROFESSIONAL SUMMARY\n"
            f"Accomplished {role_name} with {meta.get('years', 5)}+ years building mission-critical "
            f"software and distributed systems in high-growth technology companies in Vietnam.\n\n"
            f"CORE SKILLS\n"
            f"{', '.join(meta.get('skills', ['Go', 'PostgreSQL', 'Kafka', 'Kubernetes']))}\n\n"
            f"PROFESSIONAL EXPERIENCE\n"
            f"• {role_name} · {company_name} (2022 – Present)\n"
            f"  - Architected high-throughput services and reduced latency by 35%.\n"
            f"  - Owned event streaming pipelines and microservice APIs with 99.99% uptime.\n\n"
            f"EDUCATION & CERTIFICATIONS\n"
            f"• Bachelor of Engineering in Computer Science — VNU / HCMUT\n"
            f"• Languages: Vietnamese (Native), English (Professional)"
        )
        application.cv_file.save(
            "cv.pdf",
            ContentFile(make_pdf(cv_text)),
            save=True,
        )
    return application


@register_seeder(order=50)
def seed(ctx: SeedContext) -> None:
    jobs: dict[str, Job] = ctx.refs.get("jobs") or {
        job.title: job for job in Job.objects.filter(company__name="Vela Pay")
    }
    recruiters: dict[str, RecruiterProfile] = ctx.refs["recruiters"]

    applications: list[Application] = []
    backend_job = jobs["Senior Backend Engineer"]
    for name, title, nickname, stage, days_ago, duplicate in BACKEND:
        applications.append(
            _application(backend_job, name, title, recruiters[nickname], stage, days_ago, duplicate)
        )

    name, title, nickname, days_ago = DUPLICATE_TWIN
    rival = recruiters[nickname]
    email, phone, dob = demo_identity(name)
    twin_candidate, _ = Candidate.objects.update_or_create(
        full_name=name,
        owner=rival,
        defaults={"current_title": title, "email": email, "phone": phone, "dob": dob},
    )
    earlier = find_duplicate(backend_job.company, twin_candidate)
    twin = _application(backend_job, name, title, rival, Stage.NEW, days_ago, True)
    twin.duplicate_of = earlier
    twin.save(update_fields=["duplicate_of"])
    applications.append(twin)

    for job_title, name, title, nickname, stage, days_ago in OTHER:
        applications.append(
            _application(jobs[job_title], name, title, recruiters[nickname], stage, days_ago, False)
        )

    ctx.refs["applications"] = applications
    ctx.refs["pool_candidates"] = [
        _pool_candidate(row, recruiters[row["nickname"]]) for row in POOL_ONLY
    ]


@register_resetter(order=50)
def reset() -> None:
    """Applications protect the recruiter profile, so they go before the users do."""
    Application.objects.filter(recruiter__user__email__in=DEMO_EMAILS).delete()
    Candidate.objects.filter(owner__user__email__in=DEMO_EMAILS).delete()
