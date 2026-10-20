import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User, UserKind

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


def _locked_user() -> User:
    user = User.objects.create_user(
        email="locked@sparktal.vn", password="pass-1234-word", full_name="Locked Member"
    )
    user.locked_at = timezone.now()
    user.is_active = False
    user.save(update_fields=["locked_at", "is_active"])
    return user


def test_unlock_users_needs_the_change_permission(admin_client_logged_in: Client) -> None:
    """P3-R11: a staff user who may only view users cannot see or run the unlock action."""
    viewer = User.objects.create_user(
        email="viewer@sparktal.vn",
        password="pass-1234-word",
        full_name="Viewer",
        kind=UserKind.STAFF,
        is_staff=True,
    )
    viewer.user_permissions.add(Permission.objects.get(codename="view_user"))
    locked = _locked_user()
    url = "/admin/accounts/user/"

    # A superuser has the action.
    assert 'value="unlock_users"' in admin_client_logged_in.get(url).content.decode()

    client = Client()
    assert client.login(email="viewer@sparktal.vn", password="pass-1234-word")
    page = client.get(url)
    assert page.status_code == 200
    assert 'value="unlock_users"' not in page.content.decode()

    client.post(url, {"action": "unlock_users", "_selected_action": [str(locked.pk)]})

    locked.refresh_from_db()
    assert locked.locked_at is not None
    assert locked.is_active is False


def test_unlock_users_unlocks_for_a_user_who_can_change(admin_client_logged_in: Client) -> None:
    locked = _locked_user()

    admin_client_logged_in.post(
        "/admin/accounts/user/", {"action": "unlock_users", "_selected_action": [str(locked.pk)]}
    )

    locked.refresh_from_db()
    assert locked.locked_at is None
