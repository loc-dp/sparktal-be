from apps.accounts.models import User, UserKind
from apps.core.seed import DEMO_PASSWORD, SeedContext, register_seeder

DEMO_USERS: list[tuple[str, str, str]] = [
    ("nhung.le@velapay.vn", "Lê Hồng Nhung", UserKind.COMPANY),
    ("maianh.tran@velapay.vn", "Trần Mai Anh", UserKind.COMPANY),
    ("khoa.nguyen@velapay.vn", "Nguyễn Minh Khoa", UserKind.COMPANY),
    ("huy.phan@velapay.vn", "Phan Đức Huy", UserKind.COMPANY),
    ("northstar@example.com", "Võ Thanh Bình", UserKind.RECRUITER),
    ("bluefin@example.com", "Lý Hải Yến", UserKind.RECRUITER),
    ("atlas@example.com", "Hà Quang Minh", UserKind.RECRUITER),
    ("admin@sparktal.vn", "Sparktal Admin", UserKind.STAFF),
]


@register_seeder(order=10)
def seed(ctx: SeedContext) -> None:
    users: dict[str, User] = ctx.refs.setdefault("users", {})
    for email, full_name, kind in DEMO_USERS:
        is_staff = kind == UserKind.STAFF
        user, _ = User.objects.update_or_create(
            email=email,
            defaults={
                "full_name": full_name,
                "kind": kind,
                "is_active": True,
                "is_staff": is_staff,
                "is_superuser": is_staff,
            },
        )
        user.set_password(DEMO_PASSWORD)
        user.save(update_fields=["password"])
        users[email] = user
