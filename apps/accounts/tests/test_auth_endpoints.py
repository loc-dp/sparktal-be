from typing import cast

import pytest
from django.core import mail
from rest_framework.test import APIClient

from apps.accounts.models import User, UserKind
from apps.accounts.tests.factories import DEFAULT_TEST_PASSWORD, UserFactory

pytestmark = pytest.mark.django_db

LOGIN_URL = "/_allauth/browser/v1/auth/login"
SESSION_URL = "/_allauth/browser/v1/auth/session"
SIGNUP_URL = "/_allauth/browser/v1/auth/signup"
PASSWORD_REQUEST_URL = "/_allauth/browser/v1/auth/password/request"
PASSWORD_RESET_URL = "/_allauth/browser/v1/auth/password/reset"


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def test_login_with_correct_password_returns_authenticated_session(client: APIClient) -> None:
    UserFactory(email="maianh.tran@velapay.vn", full_name="Trần Mai Anh")

    response = client.post(
        LOGIN_URL,
        {"email": "maianh.tran@velapay.vn", "password": DEFAULT_TEST_PASSWORD},
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["is_authenticated"] is True
    assert body["data"]["user"]["email"] == "maianh.tran@velapay.vn"
    assert "sessionid" in response.cookies


def test_login_with_wrong_password_returns_400_with_errors(client: APIClient) -> None:
    UserFactory(email="maianh.tran@velapay.vn")

    response = client.post(
        LOGIN_URL, {"email": "maianh.tran@velapay.vn", "password": "wrong-password"}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["errors"], "allauth returns a non-empty errors array"


def test_login_of_inactive_user_fails(client: APIClient) -> None:
    UserFactory(email="left@velapay.vn", is_active=False)

    response = client.post(
        LOGIN_URL, {"email": "left@velapay.vn", "password": DEFAULT_TEST_PASSWORD}, format="json"
    )

    assert response.status_code == 401
    assert response.json()["meta"]["is_authenticated"] is False


def test_session_is_401_when_anonymous(client: APIClient) -> None:
    response = client.get(SESSION_URL)

    assert response.status_code == 401
    assert response.json()["meta"]["is_authenticated"] is False


def test_session_then_logout(client: APIClient) -> None:
    UserFactory(email="maianh.tran@velapay.vn")
    client.post(
        LOGIN_URL,
        {"email": "maianh.tran@velapay.vn", "password": DEFAULT_TEST_PASSWORD},
        format="json",
    )

    assert client.get(SESSION_URL).status_code == 200

    logout = client.delete(SESSION_URL)

    assert logout.status_code == 401
    assert client.get(SESSION_URL).status_code == 401


def test_repeated_failures_trip_the_login_rate_limit(client: APIClient) -> None:
    from django.core.cache import cache

    cache.clear()  # the limit counts in the cache, shared across tests
    UserFactory(email="maianh.tran@velapay.vn")
    credentials = {"email": "maianh.tran@velapay.vn", "password": "wrong-password"}

    codes = []
    for _ in range(7):
        response = client.post(LOGIN_URL, credentials, format="json")
        codes.append([error.get("code") for error in response.json()["errors"]])

    assert codes[-1] == ["too_many_login_attempts"]
    assert "too_many_login_attempts" not in codes[0]
    cache.clear()


def test_signup_is_closed(client: APIClient) -> None:
    response = client.post(
        SIGNUP_URL, {"email": "intruder@example.com", "password": "pass-1234-word"}, format="json"
    )

    assert response.status_code == 403


def test_password_reset_flow_sets_a_new_password(client: APIClient) -> None:
    user = cast(User, UserFactory(email="khoa.nguyen@velapay.vn", kind=UserKind.COMPANY))

    request_response = client.post(
        PASSWORD_REQUEST_URL, {"email": "khoa.nguyen@velapay.vn"}, format="json"
    )

    assert request_response.status_code == 200
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert "http://localhost:3000/reset-password?key=" in body

    key = body.split("reset-password?key=")[1].split()[0].strip()
    reset_response = client.post(
        PASSWORD_RESET_URL, {"key": key, "password": "brand-new-pass-1"}, format="json"
    )

    assert reset_response.status_code in (200, 401)  # 401 = reset ok, login not yet performed
    user.refresh_from_db()
    assert user.check_password("brand-new-pass-1")


def test_password_reset_request_for_unknown_email_reveals_nothing(client: APIClient) -> None:
    response = client.post(PASSWORD_REQUEST_URL, {"email": "nobody@example.com"}, format="json")

    assert response.status_code == 200
    assert len(mail.outbox) == 1
    assert "No Sparktal account" in mail.outbox[0].subject
    assert "reset-password?key=" not in mail.outbox[0].body
