import pytest
from django.urls import reverse
from rest_framework.test import APIClient


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def test_health_returns_ok(client: APIClient) -> None:
    response = client.get(reverse("health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_needs_no_authentication(client: APIClient) -> None:
    assert client.get("/api/v1/health").status_code == 200


def test_csrf_sets_cookie_and_returns_204(client: APIClient) -> None:
    response = client.get(reverse("csrf"))

    assert response.status_code == 204
    assert "csrftoken" in response.cookies
