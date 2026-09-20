import pytest
from django.core.management import call_command

from apps.accounts.models import User, UserKind
from apps.core.seed import DEMO_PASSWORD

pytestmark = pytest.mark.django_db


def test_seed_demo_creates_demo_users() -> None:
    call_command("seed_demo")

    ta = User.objects.get(email="maianh.tran@velapay.vn")
    assert ta.kind == UserKind.COMPANY
    assert ta.check_password(DEMO_PASSWORD)
    assert User.objects.get(email="northstar@example.com").kind == UserKind.RECRUITER
    assert User.objects.get(email="admin@sparktal.vn").is_superuser


def test_seed_demo_is_idempotent() -> None:
    call_command("seed_demo")
    before = User.objects.count()

    call_command("seed_demo")

    assert User.objects.count() == before


def test_seed_demo_reset_recreates_users() -> None:
    call_command("seed_demo")
    User.objects.filter(email="maianh.tran@velapay.vn").update(full_name="Changed")

    call_command("seed_demo", "--reset")

    assert User.objects.get(email="maianh.tran@velapay.vn").full_name == "Trần Mai Anh"
