from typing import cast

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User, UserKind
from apps.accounts.tests.factories import UserFactory

OVERVIEW = "/api/v1/ops/overview"
STAFF_ONLY = "Sparktal staff only."


@pytest.fixture
def staff_without_is_staff(db: None) -> User:
    """Staff by kind, but without the Django `is_staff` flag."""
    return cast(User, UserFactory(kind=UserKind.STAFF, is_staff=False))


@pytest.fixture
def is_staff_but_company(db: None) -> User:
    """The Django flag alone is not enough: the account must be a staff kind too."""
    return cast(User, UserFactory(kind=UserKind.COMPANY, is_staff=True))


@pytest.fixture
def inactive_staff(db: None) -> User:
    return cast(User, UserFactory(kind=UserKind.STAFF, is_staff=True, is_active=False))


@pytest.mark.parametrize(
    "who",
    [
        "anonymous",
        "company_admin",
        "recruiter_user",
        "staff_without_is_staff",
        "is_staff_but_company",
        "inactive_staff",
    ],
)
def test_ops_is_staff_only(who: str, api_client: APIClient, request: pytest.FixtureRequest) -> None:
    if who != "anonymous":
        api_client.force_login(request.getfixturevalue(who))
    # Anonymous is 403 too: the API uses session auth, which has no WWW-Authenticate challenge.
    assert api_client.get(OVERVIEW).status_code == 403


@pytest.mark.parametrize(
    "who", ["company_admin", "recruiter_user", "staff_without_is_staff", "is_staff_but_company"]
)
def test_signed_in_non_staff_is_told_why(
    who: str, api_client: APIClient, request: pytest.FixtureRequest
) -> None:
    api_client.force_login(request.getfixturevalue(who))
    response = api_client.get(OVERVIEW)
    assert response.status_code == 403
    assert response.json()["detail"] == STAFF_ONLY


def test_staff_gets_in(staff_client: APIClient) -> None:
    assert staff_client.get(OVERVIEW).status_code == 200


def test_superuser_made_by_the_manager_gets_in(db: None) -> None:
    root = User.objects.create_superuser("root@sparktal.com", "pass-1234-word", full_name="Root")
    client = APIClient()
    client.force_login(root)
    assert client.get(OVERVIEW).status_code == 200
