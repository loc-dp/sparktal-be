from datetime import date

import pytest
from rest_framework.test import APIClient

from apps.candidates.tests.factories import CandidateFactory
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db

MATCHES = "/api/v1/candidates/matches"


def matched(client: APIClient, **identity: object) -> list[str]:
    response = client.post(MATCHES, identity, format="json")
    assert response.status_code == 200, response.json()
    return [row["full_name"] for row in response.json()]


def test_an_email_in_another_case_finds_the_profile(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    CandidateFactory(owner=recruiter_profile, full_name="Trần Thu Hà", email="thuha@mail.vn")

    assert matched(recruiter_client, email="  ThuHa@Mail.VN ") == ["Trần Thu Hà"]


def test_a_local_phone_finds_the_profile_saved_in_international_format(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    CandidateFactory(owner=recruiter_profile, full_name="Lê Văn Nam", phone="+84901234567")

    assert matched(recruiter_client, phone="0901 234 567") == ["Lê Văn Nam"]


def test_name_and_birthday_together_find_the_profile_without_diacritics(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    CandidateFactory(owner=recruiter_profile, full_name="Đỗ Hải Yến", dob=date(1994, 3, 2))

    assert matched(recruiter_client, full_name="do hai yen", dob="1994-03-02") == ["Đỗ Hải Yến"]
    assert matched(recruiter_client, full_name="Đỗ Hải Yến") == []


def test_other_recruiters_pools_stay_invisible(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    CandidateFactory(full_name="Someone else's", email="shared@mail.vn")

    assert matched(recruiter_client, email="shared@mail.vn") == []


def test_nothing_to_compare_or_an_unparseable_phone_matches_nobody(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    CandidateFactory(owner=recruiter_profile, full_name="No contact", email="", phone="")

    assert matched(recruiter_client) == []
    assert matched(recruiter_client, email="", phone="not a phone") == []


def test_a_row_says_whether_the_profile_already_has_a_cv(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    CandidateFactory(owner=recruiter_profile, full_name="Ha", email="ha@mail.vn")

    row = recruiter_client.post(MATCHES, {"email": "ha@mail.vn"}, format="json").json()[0]

    assert row["has_cv"] is False
    assert "email_hash" not in row


def test_only_approved_recruiters_may_ask(api_client: APIClient) -> None:
    assert api_client.post(MATCHES, {"email": "a@b.vn"}, format="json").status_code in (401, 403)
