from typing import cast

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User, UserKind
from apps.accounts.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

ME_URL = "/api/v1/me"


def test_me_returns_the_current_user(auth_client: APIClient, user: User) -> None:
    response = auth_client.get(ME_URL)

    assert response.status_code == 200
    assert response.json() == {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "kind": "company",
        "company": None,
        "recruiter": None,
    }


def test_me_requires_authentication(api_client: APIClient) -> None:
    assert api_client.get(ME_URL).status_code == 403


def test_me_reports_recruiter_kind(api_client: APIClient) -> None:
    recruiter = cast(User, UserFactory(kind=UserKind.RECRUITER))
    api_client.force_login(recruiter)

    assert api_client.get(ME_URL).json()["kind"] == "recruiter"
