import hashlib
import unicodedata
from datetime import date, timedelta
from typing import Any

from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.text import slugify

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
    ("Hoàng Bảo Ngọc", "Backend Lead · VNG", "Bluefin Search", Stage.NEW, 0, True),
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

POOL_ONLY: list[dict[str, Any]] = [
    {
        "nickname": "NorthStar Talent",
        "full_name": "Đỗ Hải Yến",
        "current_title": "Frontend Engineer · Shopee",
        "skills": ["React", "TypeScript", "Next.js"],
        "years_experience": 6,
        "dob": date(1994, 3, 8),
        "expected_salary": 45_000_000,
    },
    {
        "nickname": "NorthStar Talent",
        "full_name": "Ngô Tuấn Kiệt",
        "current_title": "Fullstack Developer · MoMo",
        "skills": ["React", "Node.js", "GraphQL"],
        "years_experience": 5,
        "dob": date(1995, 11, 22),
        "expected_salary": 42_000_000,
    },
    {
        "nickname": "NorthStar Talent",
        "full_name": "Lương Bảo Trâm",
        "current_title": "Junior Frontend Developer · Sapo",
        "skills": ["React", "Redux"],
        "years_experience": 1,
        "dob": date(2001, 5, 2),
        "expected_salary": 18_000_000,
    },
    {
        "nickname": "NorthStar Talent",
        "full_name": "Hồ Quốc Duy",
        "current_title": "Backend Engineer · Zalo",
        "skills": ["Go", "Kafka", "PostgreSQL"],
        "years_experience": 8,
        "dob": date(1992, 1, 17),
        "expected_salary": 58_000_000,
    },
    {
        "nickname": "Bluefin Search",
        "full_name": "Nguyễn Kim Chi",
        "current_title": "Frontend Engineer · VNG",
        "skills": ["React", "Vue", "CSS"],
        "years_experience": 4,
        "dob": date(1996, 9, 30),
        "expected_salary": 36_000_000,
    },
    {
        "nickname": "Bluefin Search",
        "full_name": "Marco Alvarez",
        "current_title": "Fullstack Engineer · Grab",
        "skills": ["React", "Python", "AWS"],
        "years_experience": 9,
        "dob": date(1989, 7, 12),
        "expected_salary": 85_000_000,
        "is_expat": True,
        "languages": ["English", "Spanish"],
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
    candidate, _ = Candidate.objects.update_or_create(
        full_name=name,
        owner=recruiter,
        defaults={"current_title": title, "email": f"{abs(hash(name)) % 10_000}@example.com"},
    )
    submitted = timezone.now() - timedelta(days=days_ago)
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
        },
    )
    if created or not application.cv_file:
        application.cv_file.save(
            "cv.pdf",
            ContentFile(
                make_pdf(f"{name}\n{title}\n\nExperience\n- 6 years building payment systems")
            ),
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
