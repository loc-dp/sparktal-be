import pytest
from django.core import mail
from django.core.management import CommandError, call_command

from apps.accounts.invites import InviteError
from apps.accounts.models import User, UserKind
from apps.companies.models import Company, CompanyMembership, CompanyRole

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

    with pytest.raises(CommandError, match="already belongs to a Sparktal account"):
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

    company = Company.objects.get(name="Nordhaven")
    assert company.profile_completed is False
    assert company.memberships.get().user.email == "admin@nordhaven.vn"
    assert company.memberships.get().role == "admin"


def test_create_company_command_still_works() -> None:
    call_command(
        "create_company",
        "--name",
        "  Nordhaven  ",
        "--admin-email",
        "admin@nordhaven.vn",
        "--admin-name",
        "Phạm Quốc Anh",
    )

    membership = CompanyMembership.objects.select_related("company", "user").get(
        user__email="admin@nordhaven.vn"
    )
    assert membership.company.name == "Nordhaven"
    assert membership.role == CompanyRole.ADMIN
    assert membership.user.kind == UserKind.COMPANY
    assert not membership.user.has_usable_password()
    assert [m.to for m in mail.outbox] == [["admin@nordhaven.vn"]]
    assert "The Sparktal team has invited you to join Nordhaven" in mail.outbox[0].body


def test_rejects_a_company_name_over_120_characters() -> None:
    with pytest.raises(CommandError, match="1 to 120 characters"):
        call_command(
            "create_company",
            "--name",
            "x" * 121,
            "--admin-email",
            "admin@nordhaven.vn",
            "--admin-name",
            "Anh",
        )

    assert not User.objects.filter(email="admin@nordhaven.vn").exists()


def test_a_failed_invite_saves_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(email: str, **details: str | None) -> None:
        raise InviteError("smtp down")

    monkeypatch.setattr(
        "apps.accounts.management.commands.create_company.send_password_invite", boom
    )

    with pytest.raises(CommandError, match="Could not send the invite"):
        call_command(
            "create_company",
            "--name",
            "Nordhaven",
            "--admin-email",
            "admin@nordhaven.vn",
            "--admin-name",
            "Anh",
        )

    assert not Company.objects.filter(name="Nordhaven").exists()
    assert not User.objects.filter(email="admin@nordhaven.vn").exists()
