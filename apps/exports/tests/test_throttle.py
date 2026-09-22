import pytest
from django.core.cache import cache
from django.test import override_settings

from apps.accounts.models import User
from apps.exports.constants import RATE_LIMIT_MAX
from apps.exports.throttle import consume_export_quota

LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "throttle-unit",
    }
}

pytestmark = pytest.mark.django_db


@override_settings(CACHES=LOCMEM)
def test_the_first_five_calls_pass_and_the_sixth_waits(user: User) -> None:
    cache.clear()

    assert [consume_export_quota(user) for _ in range(RATE_LIMIT_MAX)] == [None] * RATE_LIMIT_MAX

    wait = consume_export_quota(user)
    assert isinstance(wait, int)
    assert wait > 0


@override_settings(CACHES=LOCMEM)
def test_quota_is_per_user(user: User, company_ta: User) -> None:
    cache.clear()
    for _ in range(RATE_LIMIT_MAX + 1):
        consume_export_quota(user)

    assert consume_export_quota(company_ta) is None
