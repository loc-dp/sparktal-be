import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.accounts.models import User, UserKind
from apps.core.seed import DEMO_PASSWORD

pytestmark = pytest.mark.django_db


def test_seed_demo_creates_demo_users() -> None:
    call_command("seed_demo", "--force")

    ta = User.objects.get(email="maianh.tran@velapay.vn")
    assert ta.kind == UserKind.COMPANY
    assert ta.check_password(DEMO_PASSWORD)
    assert User.objects.get(email="northstar@example.com").kind == UserKind.RECRUITER
    assert User.objects.get(email="admin@sparktal.vn").is_superuser


def test_seed_demo_is_idempotent() -> None:
    call_command("seed_demo", "--force")
    before = User.objects.count()

    call_command("seed_demo", "--force")

    assert User.objects.count() == before


def test_seed_demo_reset_recreates_users() -> None:
    call_command("seed_demo", "--force")
    User.objects.filter(email="maianh.tran@velapay.vn").update(full_name="Changed")

    call_command("seed_demo", "--reset", "--force")

    assert User.objects.get(email="maianh.tran@velapay.vn").full_name == "Trần Mai Anh"


def test_seed_demo_unlocks_the_demo_users_it_resets() -> None:
    call_command("seed_demo", "--force")
    emails = ["maianh.tran@velapay.vn", "tuan.vo@velapay.vn", "new.admin@example.com"]
    User.objects.filter(email__in=emails).update(is_active=False, locked_at=timezone.now())

    call_command("seed_demo", "--reset", "--force")

    for user in User.objects.filter(email__in=emails):
        assert user.locked_at is None
        assert user.is_active is True


def test_seed_demo_refuses_to_run_with_debug_off() -> None:
    with pytest.raises(CommandError, match="publicly known password"):
        call_command("seed_demo")
