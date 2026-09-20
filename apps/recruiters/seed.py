from apps.accounts.models import User
from apps.core.seed import SeedContext, register_seeder
from apps.recruiters.models import RecruiterProfile

RECRUITERS = [
    ("northstar@example.com", "NorthStar Talent"),
    ("bluefin@example.com", "Bluefin Search"),
    ("atlas@example.com", "Atlas Hunters"),
]


@register_seeder(order=30)
def seed(ctx: SeedContext) -> None:
    users: dict[str, User] = ctx.refs.setdefault("users", {})
    profiles: dict[str, RecruiterProfile] = {}
    for email, nickname in RECRUITERS:
        user = users.get(email) or User.objects.get(email=email)
        profile, _ = RecruiterProfile.objects.update_or_create(
            user=user, defaults={"nickname": nickname}
        )
        profiles[nickname] = profile
    ctx.refs["recruiters"] = profiles
