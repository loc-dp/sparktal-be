import pytest
from django.core import mail
from django.core.management import CommandError, call_command

from apps.accounts.models import User, UserKind

pytestmark = pytest.mark.django_db


def test_creates_an_unusable_password_company_admin_and_sends_an_invite() -> None:
    call_command(
        "create_company",
        "--name",
        "Vela Pay",
        "--admin-email",
        "nhung.le@velapay.vn",
        "--admin-name",
        "Lê Hồng Nhung",
    )

    user = User.objects.get(email="nhung.le@velapay.vn")
    assert user.kind == UserKind.COMPANY
    assert not user.has_usable_password()
    assert len(mail.outbox) == 1
    assert "reset-password?key=" in mail.outbox[0].body


def test_no_email_flag_skips_the_invite() -> None:
    call_command(
        "create_company",
        "--name",
        "Vela Pay",
        "--admin-email",
        "nhung.le@velapay.vn",
        "--admin-name",
        "Lê Hồng Nhung",
        "--no-email",
    )

    assert mail.outbox == []


@pytest.mark.parametrize("hosts", [["localhost", "127.0.0.1"], ["api.sparktal.com"]])
def test_invite_uses_an_allowed_host(settings, hosts: list[str]) -> None:  # type: ignore[no-untyped-def]
    # RequestFactory defaults to "testserver", which only ALLOWED_HOSTS in tests
    # accepts — the command has to pick a host the project really allows.
    settings.ALLOWED_HOSTS = hosts

    call_command(
        "create_company",
        "--name",
        "Vela Pay",
        "--admin-email",
        "host.check@velapay.vn",
        "--admin-name",
        "Host Check",
    )

    assert len(mail.outbox) == 1


def test_rejects_an_existing_email() -> None:
    User.objects.create_user(email="taken@velapay.vn", full_name="Taken")

    with pytest.raises(CommandError, match="already exists"):
        call_command(
            "create_company",
            "--name",
            "Vela Pay",
            "--admin-email",
            "taken@velapay.vn",
            "--admin-name",
            "Taken",
        )


def test_creates_the_company_and_an_admin_membership() -> None:
    call_command(
        "create_company",
        "--name",
        "Nordhaven",
        "--admin-email",
        "admin@nordhaven.vn",
        "--admin-name",
        "Phạm Quốc Anh",
        "--no-email",
    )

    from apps.companies.models import Company

    company = Company.objects.get(name="Nordhaven")
    assert company.profile_completed is False
    assert company.memberships.get().user.email == "admin@nordhaven.vn"
    assert company.memberships.get().role == "admin"
