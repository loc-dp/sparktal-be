"""Registry for demo-data seeders used by `manage.py seed_demo`.

Each app that ships demo data has an `apps/<app>/seed.py` with:

    @register_seeder(order=20)
    def seed(ctx: SeedContext) -> None: ...

Orders already assigned: accounts 10, companies 20, recruiters 30, jobs 40,
candidates 50, chat 70. Seeders must be idempotent (use `update_or_create`).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DEMO_PASSWORD = "sparktal-demo-2026"

DEMO_EMAILS: list[str] = [
    "nhung.le@velapay.vn",
    "maianh.tran@velapay.vn",
    "khoa.nguyen@velapay.vn",
    "huy.phan@velapay.vn",
    "northstar@example.com",
    "bluefin@example.com",
    "atlas@example.com",
    "admin@sparktal.vn",
]


@dataclass
class SeedContext:
    refs: dict[str, Any] = field(default_factory=dict)


Seeder = Callable[[SeedContext], None]

_REGISTRY: dict[str, tuple[int, Seeder]] = {}


def register_seeder(order: int) -> Callable[[Seeder], Seeder]:
    """Register `fn` to run at position `order` (lower runs first)."""

    def decorator(fn: Seeder) -> Seeder:
        _REGISTRY[f"{fn.__module__}.{fn.__qualname__}"] = (order, fn)
        return fn

    return decorator


def run_seeders(ctx: SeedContext) -> list[str]:
    """Run every registered seeder in `order`; return their dotted names in run order."""
    ran: list[str] = []
    for name, (_order, fn) in sorted(_REGISTRY.items(), key=lambda item: (item[1][0], item[0])):
        fn(ctx)
        ran.append(name)
    return ran
