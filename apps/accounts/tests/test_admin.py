import pytest
from django.test import Client

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client_logged_in() -> Client:
    User.objects.create_superuser(
        email="admin@sparktal.vn", password="pass-1234-word", full_name="Sparktal Admin"
    )
    client = Client()
    client.login(email="admin@sparktal.vn", password="pass-1234-word")
    return client


def test_admin_user_changelist_loads(admin_client_logged_in: Client) -> None:
    assert admin_client_logged_in.get("/admin/accounts/user/").status_code == 200


def test_admin_can_create_user(admin_client_logged_in: Client) -> None:
    response = admin_client_logged_in.post(
        "/admin/accounts/user/add/",
        {
            "email": "new.user@velapay.vn",
            "full_name": "New User",
            "kind": "company",
            "password1": "pass-1234-word",
            "password2": "pass-1234-word",
        },
    )

    assert response.status_code == 302
    assert User.objects.filter(email="new.user@velapay.vn").exists()
