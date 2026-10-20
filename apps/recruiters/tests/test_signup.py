import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import User, UserKind
from apps.accounts.tests.factories import UserFactory
from apps.recruiters.models import RecruiterProfile, RecruiterStatus
from apps.recruiters.serializers import (
    EMAIL_TAKEN,
    NICKNAME_TAKEN,
    SignupSerializer,
    clean_nickname,
)
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

PAYLOAD = {
    "email": "linh.dang.hr@gmail.com",
    "full_name": "Đặng Thuỳ Linh",
    "nickname": "Lantern Talent",
    "password": "N0rthStar!2026",
    "agreed_terms": True,
}


def test_signing_up_creates_a_pending_recruiter(api_client: APIClient) -> None:
    response = api_client.post("/api/v1/recruiters/signup", PAYLOAD, format="json")

    assert response.status_code == 201
    assert response.json()["status"] == RecruiterStatus.PENDING
    user = User.objects.get(email=PAYLOAD["email"])
    assert user.kind == UserKind.RECRUITER
    assert user.recruiter.nickname == "Lantern Talent"
    assert user.recruiter.agreed_terms_at is not None
    assert user.recruiter.submitted_at is not None


def test_signup_logs_in_the_new_recruiter(api_client: APIClient) -> None:
    api_client.post("/api/v1/recruiters/signup", PAYLOAD, format="json")
    response = api_client.get("/api/v1/me")
    assert response.status_code == 200
    assert response.json()["recruiter"]["status"] == "pending"


def test_duplicate_email_is_rejected(api_client: APIClient, recruiter_user: User) -> None:
    response = api_client.post(
        "/api/v1/recruiters/signup", {**PAYLOAD, "email": recruiter_user.email}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["email"] == ["An account with this email already exists."]


@pytest.mark.parametrize(
    "winner",
    ["linh.dang.hr@gmail.com", "Linh.Dang.HR@Gmail.com"],
    ids=["same-case", "mixed-case"],
)
def test_losing_the_race_for_the_email_is_a_400_not_a_500(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch, winner: str
) -> None:
    # The address was free when the form was checked, and taken when the account was made.
    # The factory stores it as written, so the mixed-case row differs from ours only in case.
    UserFactory(email=winner, kind=UserKind.RECRUITER)
    monkeypatch.setattr(SignupSerializer, "validate_email", lambda self, value: value.lower())
    users = User.objects.count()

    response = api_client.post("/api/v1/recruiters/signup", PAYLOAD, format="json")

    assert response.status_code == 400
    assert response.json() == {"email": [EMAIL_TAKEN]}
    assert User.objects.count() == users
    assert not RecruiterProfile.objects.filter(nickname=PAYLOAD["nickname"]).exists()
    assert api_client.get("/api/v1/me").status_code in (401, 403)


def test_losing_the_race_for_the_nickname_is_a_400_not_a_500(
    api_client: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    RecruiterProfileFactory(nickname="Lantern Talent")
    # The nickname was free when the form was checked, and taken when the profile was made.
    monkeypatch.setattr(
        SignupSerializer, "validate_nickname", lambda self, value: clean_nickname(value)
    )
    users = User.objects.count()

    response = api_client.post(
        "/api/v1/recruiters/signup", {**PAYLOAD, "nickname": "lantern talent"}, format="json"
    )

    assert response.status_code == 400
    assert response.json() == {"nickname": [NICKNAME_TAKEN]}
    assert User.objects.count() == users
    assert not User.objects.filter(email=PAYLOAD["email"]).exists()
    assert api_client.get("/api/v1/me").status_code in (401, 403)


def test_duplicate_nickname_is_rejected_without_half_account(api_client: APIClient) -> None:
    RecruiterProfileFactory(nickname="Lantern Talent")
    response = api_client.post(
        "/api/v1/recruiters/signup", {**PAYLOAD, "nickname": "lantern talent"}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["nickname"] == ["That nickname is taken."]
    assert not User.objects.filter(email=PAYLOAD["email"]).exists()


def test_weak_password_is_rejected(api_client: APIClient) -> None:
    response = api_client.post(
        "/api/v1/recruiters/signup", {**PAYLOAD, "password": "password"}, format="json"
    )
    assert response.status_code == 400
    assert "password" in response.json()


def test_terms_are_required(api_client: APIClient) -> None:
    response = api_client.post(
        "/api/v1/recruiters/signup", {**PAYLOAD, "agreed_terms": False}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["agreed_terms"] == ["Accept the marketplace rules to continue."]


def test_nickname_check_is_case_insensitive(api_client: APIClient) -> None:
    RecruiterProfileFactory(nickname="Lantern Talent")
    assert api_client.get(
        "/api/v1/recruiters/nickname-check", {"nickname": "LANTERN talent"}
    ).json() == {"available": False}
    assert api_client.get(
        "/api/v1/recruiters/nickname-check", {"nickname": "Harbour Talent"}
    ).json() == {"available": True}


def test_nickname_rejects_underscores(api_client: APIClient) -> None:
    response = api_client.post(
        "/api/v1/recruiters/signup",
        {**PAYLOAD, "nickname": "Lantern_Talent"},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["nickname"] == [
        "Use 3 to 60 characters: letters, numbers, spaces, & . -"
    ]


def test_signup_is_rate_limited_per_ip(api_client: APIClient) -> None:
    cache.clear()
    for _ in range(5):
        response = api_client.post(
            "/api/v1/recruiters/signup",
            {**PAYLOAD, "nickname": "hi"},
            format="json",
            REMOTE_ADDR="203.0.113.86",
        )
        assert response.status_code == 400
    response = api_client.post(
        "/api/v1/recruiters/signup",
        {**PAYLOAD, "nickname": "hi"},
        format="json",
        REMOTE_ADDR="203.0.113.86",
    )
    assert response.status_code == 429
