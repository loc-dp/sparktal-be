from typing import Any

from django.utils import timezone

from apps.accounts.models import User
from apps.core.seed import SeedContext, register_resetter, register_seeder
from apps.recruiters.models import RecruiterProfile, RecruiterStatus

E2E_EMAIL_SUFFIX = "@e2e.example.com"

RECRUITERS: list[tuple[str, str, str]] = [
    ("northstar@example.com", "NorthStar Talent", RecruiterStatus.APPROVED),
    ("bluefin@example.com", "Bluefin Search", RecruiterStatus.APPROVED),
    ("atlas@example.com", "Atlas Hunters", RecruiterStatus.PENDING),
    ("quasar@example.com", "Quasar Sourcing", RecruiterStatus.REJECTED),
]

RECRUITER_DATA: dict[str, dict[str, Any]] = {
    "NorthStar Talent": {
        "status": RecruiterStatus.APPROVED,
        "bio": (
            "Premier boutique executive search agency specializing in Tier-1 Fintech, "
            "Payments and Banking technology leadership across Southeast Asia. Over 100 "
            "senior engineering placements in Vietnam since 2018."
        ),
        "expertise": ["finance", "engineering", "data"],
        "years_experience": 8,
        "linkedin": "https://www.linkedin.com/company/northstar-talent-vn",
        "review_note": "Verified top-tier headhunting partner. Consistently delivers pre-vetted senior profiles within 72 hours.",
    },
    "Bluefin Search": {
        "status": RecruiterStatus.APPROVED,
        "bio": (
            "High-velocity technology recruitment agency focused on E-commerce, Consumer Tech "
            "and SaaS scale-ups in Ho Chi Minh City, Hanoi and Singapore."
        ),
        "expertise": ["engineering", "design", "operations"],
        "years_experience": 6,
        "linkedin": "https://www.linkedin.com/company/bluefin-search-asia",
        "review_note": "Strong talent network in consumer internet and modern frontend/backend frameworks.",
    },
    "Atlas Hunters": {
        "status": RecruiterStatus.PENDING,
        "bio": (
            "Specialist headhunting practice dedicated to Systems Architecture, Cloud Infrastructure, "
            "and SRE/Cybersecurity talent across Vietnam's tech hubs."
        ),
        "expertise": ["engineering", "operations", "finance"],
        "years_experience": 5,
        "linkedin": "https://www.linkedin.com/company/atlas-hunters",
        "review_note": (
            "Pending business license and tax code verification from Department of Planning and "
            "Investment. Track record references verified."
        ),
    },
    "Quasar Sourcing": {
        "status": RecruiterStatus.REJECTED,
        "bio": (
            "Volume sourcing for junior and mid-level technology roles, working from "
            "a shared candidate database across Vietnam."
        ),
        "expertise": ["other"],
        "years_experience": 1,
        "linkedin": "https://www.linkedin.com/company/quasar-sourcing",
        "review_note": (
            "We could not verify a business registration or any placement history. "
            "Please reapply with your business license and two client references."
        ),
    },
}


@register_seeder(order=30)
def seed(ctx: SeedContext) -> None:
    users: dict[str, User] = ctx.refs.setdefault("users", {})
    profiles: dict[str, RecruiterProfile] = {}
    for email, nickname, _status in RECRUITERS:
        user = users.get(email) or User.objects.get(email=email)
        data = RECRUITER_DATA.get(nickname, {})
        profile, _ = RecruiterProfile.objects.update_or_create(
            user=user,
            defaults={
                "nickname": nickname,
                "status": data.get("status", RecruiterStatus.APPROVED),
                "review_note": data.get("review_note", ""),
                "bio": data.get("bio", ""),
                "expertise": data.get("expertise", []),
                "years_experience": data.get("years_experience"),
                "linkedin": data.get("linkedin", ""),
            },
        )
        if profile.status == RecruiterStatus.REJECTED:
            profile.reviewed_by = users.get("admin@sparktal.vn") or User.objects.get(
                email="admin@sparktal.vn"
            )
            profile.reviewed_at = timezone.now()
            profile.save(update_fields=["reviewed_by", "reviewed_at"])
        profiles[nickname] = profile
    ctx.refs["recruiters"] = profiles


@register_resetter(order=30)
def reset() -> None:
    User.objects.filter(email__endswith=E2E_EMAIL_SUFFIX).delete()
