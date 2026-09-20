from typing import cast

from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.tests.factories import DEFAULT_TEST_PASSWORD, UserFactory


def test_user_factory_builds_company_user_with_known_password(db: None) -> None:
    user = cast(User, UserFactory())

    assert user.kind == "company"
    assert user.check_password(DEFAULT_TEST_PASSWORD)


def test_auth_client_is_logged_in_as_user(auth_client: APIClient, user: User) -> None:
    response = auth_client.get("/api/v1/health")

    assert response.status_code == 200
    assert auth_client.session["_auth_user_id"] == str(user.pk)
