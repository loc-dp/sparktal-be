"""The browser app on FRONTEND_ORIGINS calls the API cross-origin with the session cookie.

The origin under test is read from settings rather than hard-coded, so these tests check
that whatever FRONTEND_ORIGINS a developer's .env sets is the origin CORS lets in.
"""

import pytest
from django.conf import settings
from rest_framework.test import APIClient

HEALTH = "/api/v1/health"
UNKNOWN_ORIGIN = "https://evil.example.com"


@pytest.fixture
def frontend_origin() -> str:
    origin: str = settings.FRONTEND_ORIGINS[0]
    assert origin, "FRONTEND_ORIGINS must name at least one origin"
    return origin


@pytest.fixture
def unknown_origin() -> str:
    assert UNKNOWN_ORIGIN not in settings.FRONTEND_ORIGINS
    return UNKNOWN_ORIGIN


def test_simple_request_from_the_frontend_is_allowed_with_credentials(
    api_client: APIClient, frontend_origin: str
) -> None:
    response = api_client.get(HEALTH, headers={"Origin": frontend_origin})

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == frontend_origin
    assert response.headers["Access-Control-Allow-Credentials"] == "true"


def test_preflight_from_the_frontend_is_allowed_with_credentials(
    api_client: APIClient, frontend_origin: str
) -> None:
    response = api_client.options(
        HEALTH,
        headers={
            "Origin": frontend_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, x-csrftoken",
        },
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == frontend_origin
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    assert "POST" in response.headers["Access-Control-Allow-Methods"]
    # Unsafe requests from the app carry the CSRF token header alongside the session cookie.
    allowed = {name.strip() for name in response.headers["Access-Control-Allow-Headers"].split(",")}
    assert {"content-type", "x-csrftoken"} <= allowed


def test_simple_request_from_an_unknown_origin_gets_no_cors_grant(
    api_client: APIClient, unknown_origin: str
) -> None:
    response = api_client.get(HEALTH, headers={"Origin": unknown_origin})

    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers
    assert "Access-Control-Allow-Credentials" not in response.headers


def test_preflight_from_an_unknown_origin_gets_no_cors_grant(
    api_client: APIClient, unknown_origin: str
) -> None:
    response = api_client.options(
        HEALTH,
        headers={"Origin": unknown_origin, "Access-Control-Request-Method": "POST"},
    )

    assert "Access-Control-Allow-Origin" not in response.headers
    assert "Access-Control-Allow-Credentials" not in response.headers
