from typing import cast

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def user(db: None) -> User:
    return cast(User, UserFactory())


@pytest.fixture
def auth_client(user: User) -> APIClient:
    client = APIClient()
    client.force_login(user)
    return client
