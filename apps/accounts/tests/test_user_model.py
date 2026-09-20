import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from apps.accounts.models import User, UserKind

pytestmark = pytest.mark.django_db


def test_auth_user_model_is_accounts_user() -> None:
    assert get_user_model() is User


def test_create_user_normalizes_email_and_hashes_password() -> None:
    user = User.objects.create_user(
        email="  Mai.Anh@VelaPay.vn ", password="pass-1234-word", full_name="Trần Mai Anh"
    )

    assert user.email == "mai.anh@velapay.vn"
    assert user.password != "pass-1234-word"
    assert user.check_password("pass-1234-word")
    assert user.kind == UserKind.COMPANY


def test_create_user_uses_uuid_primary_key() -> None:
    user = User.objects.create_user(email="a@example.com", full_name="A")

    assert isinstance(user.pk, uuid.UUID)


def test_create_user_requires_email() -> None:
    with pytest.raises(ValueError, match="email"):
        User.objects.create_user(email="", full_name="No Email")


def test_email_is_unique() -> None:
    User.objects.create_user(email="dup@example.com", full_name="One")

    with pytest.raises(IntegrityError):
        User.objects.create_user(email="dup@example.com", full_name="Two")


def test_create_superuser_is_staff_kind() -> None:
    user = User.objects.create_superuser(
        email="admin@sparktal.vn", password="pass-1234-word", full_name="Sparktal Admin"
    )

    assert user.is_staff and user.is_superuser
    assert user.kind == UserKind.STAFF


def test_user_kind_choices() -> None:
    assert [choice[0] for choice in UserKind.choices] == ["company", "recruiter", "staff"]
