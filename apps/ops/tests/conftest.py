from typing import cast

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User, UserKind
from apps.accounts.tests.factories import UserFactory


@pytest.fixture
def staff_user(db: None) -> User:
    return cast(
        User,
        UserFactory(email="ops@sparktal.com", kind=UserKind.STAFF, is_staff=True),
    )


@pytest.fixture
def staff_client(staff_user: User) -> APIClient:
    """Signed in as Sparktal staff: the only kind of user `/api/v1/ops/*` lets in."""
    client = APIClient()
    client.force_login(staff_user)
    return client
