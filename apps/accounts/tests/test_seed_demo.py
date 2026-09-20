import pytest
from django.core.management import CommandError, call_command

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


def test_seed_demo_refuses_to_run_with_debug_off() -> None:
    # The demo accounts share a publicly known password, so the command must not
    # run outside development without an explicit --force.
    with pytest.raises(CommandError, match="publicly known password"):
        call_command("seed_demo")
