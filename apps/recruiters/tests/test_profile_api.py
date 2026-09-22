from collections.abc import Callable

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


def test_recruiter_can_read_own_profile(
    client_for: Callable[[User], APIClient], recruiter_user: User
) -> None:
    response = client_for(recruiter_user).get("/api/v1/recruiters/me")
    assert response.status_code == 200
    assert response.json()["nickname"] == "NorthStar Talent"
    assert response.json()["status"] == "approved"


def test_pending_recruiter_can_edit_but_not_approve(
    client_for: Callable[[User], APIClient], pending_recruiter_user: User
) -> None:
    response = client_for(pending_recruiter_user).patch(
        "/api/v1/recruiters/me",
        {
            "bio": "12 years in fintech hiring.",
            "expertise": ["engineering", "data"],
            "status": "approved",
        },
        format="json",
    )
    pending_recruiter_user.recruiter.refresh_from_db()
    assert response.status_code == 200
    assert response.json()["bio"] == "12 years in fintech hiring."
    assert pending_recruiter_user.recruiter.status == "pending"


def test_company_user_cannot_read_profile(
    client_for: Callable[[User], APIClient], company_admin: User
) -> None:
    assert client_for(company_admin).get("/api/v1/recruiters/me").status_code == 404


def test_review_fields_cannot_be_changed_through_profile_api(
    client_for: Callable[[User], APIClient], pending_recruiter_user: User
) -> None:
    response = client_for(pending_recruiter_user).patch(
        "/api/v1/recruiters/me",
        {
            "review_note": "Approved by me",
            "reviewed_at": "2026-09-21T00:00:00Z",
            "status": "approved",
        },
        format="json",
    )
    pending_recruiter_user.recruiter.refresh_from_db()
    assert response.status_code == 200
    assert pending_recruiter_user.recruiter.status == "pending"
    assert pending_recruiter_user.recruiter.review_note == ""
    assert pending_recruiter_user.recruiter.reviewed_at is None
