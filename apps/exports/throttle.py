"""Per-user rate limit for CV exports, backed by Django's shared cache."""

from django.core.cache import cache

from apps.accounts.models import User
from apps.exports.constants import RATE_LIMIT_MAX, RATE_LIMIT_WINDOW


def rate_limit_key(user_id: object) -> str:
    return f"cv-export-rate:{user_id}"


def _remaining_seconds(key: str) -> int:
    ttl_getter = getattr(cache, "ttl", None)
    if ttl_getter is None:
        return RATE_LIMIT_WINDOW
    ttl = ttl_getter(key)
    return int(ttl) if ttl and ttl > 0 else RATE_LIMIT_WINDOW


def consume_export_quota(user: User) -> int | None:
    """Return None when an export may start, otherwise seconds to wait."""
    key = rate_limit_key(user.pk)
    if cache.add(key, 1, RATE_LIMIT_WINDOW):
        return None
    try:
        used = cache.incr(key)
    except ValueError:
        cache.set(key, 1, RATE_LIMIT_WINDOW)
        return None
    if used > RATE_LIMIT_MAX:
        return _remaining_seconds(key)
    return None
