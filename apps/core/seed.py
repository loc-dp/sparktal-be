"""Registry for demo-data seeders used by `manage.py seed_demo`.

Each app that ships demo data has an `apps/<app>/seed.py` with:

    @register_seeder(order=20)
    def seed(ctx: SeedContext) -> None: ...

Orders already assigned: accounts 10, companies 20, recruiters 30, jobs 40,
job assignments 45, candidates 50, chat 70, hiring 80. Seeders must be idempotent
(use `update_or_create`).
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

DEMO_PASSWORD = "demo123"

DEMO_EMAILS: list[str] = [
    "nhung.le@velapay.vn",
    "maianh.tran@velapay.vn",
    "khoa.nguyen@velapay.vn",
    "huy.phan@velapay.vn",
    "northstar@example.com",
    "bluefin@example.com",
    "atlas@example.com",
    "quasar@example.com",
    "tuan.vo@velapay.vn",
    "admin@sparktal.vn",
    "new.admin@example.com",
]


@dataclass
class SeedContext:
    refs: dict[str, Any] = field(default_factory=dict)


Seeder = Callable[[SeedContext], None]
Resetter = Callable[[], None]

_REGISTRY: dict[str, tuple[int, Seeder]] = {}
_RESETTERS: dict[str, tuple[int, Resetter]] = {}


def register_seeder(order: int) -> Callable[[Seeder], Seeder]:
    """Register `fn` to run at position `order` (lower runs first)."""

    def decorator(fn: Seeder) -> Seeder:
        _REGISTRY[f"{fn.__module__}.{fn.__qualname__}"] = (order, fn)
        return fn

    return decorator


def register_resetter(order: int) -> Callable[[Resetter], Resetter]:
    """Rows an app must delete before `--reset` removes the demo users.

    Deleting a user cascades to its RecruiterProfile, which `Application.recruiter`
    protects, so every app clears its own rows first (highest order first).
    """

    def decorator(fn: Resetter) -> Resetter:
        _RESETTERS[f"{fn.__module__}.{fn.__qualname__}"] = (order, fn)
        return fn

    return decorator


def run_resetters() -> list[str]:
    ran: list[str] = []
    for name, (_order, fn) in sorted(_RESETTERS.items(), key=lambda item: (-item[1][0], item[0])):
        fn()
        ran.append(name)
    return ran


@contextmanager
def isolated_registry() -> Iterator[None]:
    """Swap in an empty registry so a test can run its own seeders alone."""
    global _REGISTRY, _RESETTERS
    saved, saved_resets = _REGISTRY, _RESETTERS
    _REGISTRY, _RESETTERS = {}, {}
    try:
        yield
    finally:
        _REGISTRY, _RESETTERS = saved, saved_resets


def run_seeders(ctx: SeedContext) -> list[str]:
    """Run every registered seeder in `order`; return their dotted names in run order."""
    ran: list[str] = []
    for name, (_order, fn) in sorted(_REGISTRY.items(), key=lambda item: (item[1][0], item[0])):
        fn(ctx)
        ran.append(name)
    return ran
