from apps.accounts.models import User
from apps.core.seed import SeedContext, register_resetter, register_seeder
from apps.recruiters.models import RecruiterProfile, RecruiterStatus

E2E_EMAIL_SUFFIX = "@e2e.example.com"

RECRUITERS = [
    ("northstar@example.com", "NorthStar Talent", RecruiterStatus.APPROVED),
    ("bluefin@example.com", "Bluefin Search", RecruiterStatus.APPROVED),
    ("atlas@example.com", "Atlas Hunters", RecruiterStatus.PENDING),
]


@register_seeder(order=30)
def seed(ctx: SeedContext) -> None:
    users: dict[str, User] = ctx.refs.setdefault("users", {})
    profiles: dict[str, RecruiterProfile] = {}
    for email, nickname, status in RECRUITERS:
        user = users.get(email) or User.objects.get(email=email)
        profile, _ = RecruiterProfile.objects.update_or_create(
            user=user,
            defaults={
                "nickname": nickname,
                "status": status,
                "review_note": "",
                "bio": "",
                "expertise": [],
                "years_experience": None,
                "linkedin": "",
            },
        )
        profiles[nickname] = profile
    ctx.refs["recruiters"] = profiles


@register_resetter(order=30)
def reset() -> None:
    User.objects.filter(email__endswith=E2E_EMAIL_SUFFIX).delete()
