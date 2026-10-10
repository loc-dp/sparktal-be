from collections.abc import Callable
from datetime import date

import pytest
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.models import Candidate
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db

CANDIDATES = "/api/v1/candidates"

ClientFor = Callable[[User], APIClient]


@pytest.fixture
def pool(recruiter_profile: RecruiterProfile) -> None:
    CandidateFactory(
        owner=recruiter_profile,
        full_name="Đỗ Hải Yến",
        current_title="Frontend Engineer",
        skills=["React", "TypeScript"],
        years_experience=6,
    )
    CandidateFactory(
        owner=recruiter_profile,
        full_name="Ngô Tuấn Kiệt",
        current_title="Fullstack Developer",
        skills=["React", "Node"],
        years_experience=4,
        is_expat=True,
    )
    CandidateFactory(
        owner=recruiter_profile,
        full_name="Lương Bảo Trâm",
        current_title="Junior Frontend Developer",
        skills=["React"],
        years_experience=1,
    )


def names(response: Response) -> list[str]:
    return [row["full_name"] for row in response.json()["results"]]


# ── list ─────────────────────────────────────────────────────────────────────


def test_the_pool_only_shows_your_own_candidates(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    CandidateFactory(owner=recruiter_profile, full_name="Mine")
    CandidateFactory(full_name="Someone else's")

    response = recruiter_client.get(CANDIDATES)

    assert response.status_code == 200
    assert names(response) == ["Mine"]
    assert response.json()["count"] == 1


def test_a_row_carries_the_whole_profile(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    CandidateFactory(
        owner=recruiter_profile,
        full_name="Trương Mỹ Hạnh",
        current_title="Payments Backend Lead",
        email="myhanh.truong@gmail.com",
        skills=["Go", "Kafka"],
        location="hcm",
        expected_salary=62_000_000,
    )

    row = recruiter_client.get(CANDIDATES).json()["results"][0]

    assert row["email"] == "myhanh.truong@gmail.com"
    assert row["skills"] == ["Go", "Kafka"]
    assert row["location"] == "hcm"
    assert row["expected_salary"] == "62000000"
    assert row["has_cv"] is False
    assert row["submitted_count"] == 0
    assert "email_hash" not in row


def test_boolean_search_filters_the_list(recruiter_client: APIClient, pool: None) -> None:
    response = recruiter_client.get(
        CANDIDATES, {"q": '("Frontend" OR "Fullstack") AND "React" NOT "Junior"'}
    )

    assert sorted(names(response)) == ["Ngô Tuấn Kiệt", "Đỗ Hải Yến"]


def test_broken_boolean_syntax_is_a_400(recruiter_client: APIClient, pool: None) -> None:
    response = recruiter_client.get(CANDIDATES, {"q": '("React"'})

    assert response.status_code == 400
    assert response.json()["q"] == ["Check the Boolean syntax."]


def test_the_expat_filter_narrows_the_list(recruiter_client: APIClient, pool: None) -> None:
    # No parameter means "Include expat" — the prototype chip starts pressed.
    assert "Ngô Tuấn Kiệt" in names(recruiter_client.get(CANDIDATES))
    assert "Ngô Tuấn Kiệt" not in names(recruiter_client.get(CANDIDATES, {"is_expat": "false"}))
    assert names(recruiter_client.get(CANDIDATES, {"is_expat": "true"})) == ["Ngô Tuấn Kiệt"]


def test_the_skills_filter_takes_a_comma_separated_list(
    recruiter_client: APIClient, pool: None
) -> None:
    assert names(recruiter_client.get(CANDIDATES, {"skills": "TypeScript"})) == ["Đỗ Hải Yến"]
    assert sorted(names(recruiter_client.get(CANDIDATES, {"skills": "TypeScript,Node"}))) == [
        "Ngô Tuấn Kiệt",
        "Đỗ Hải Yến",
    ]


def test_ordering_is_whitelisted(recruiter_client: APIClient, pool: None) -> None:
    ordered = recruiter_client.get(CANDIDATES, {"ordering": "-years_experience"})

    assert names(ordered) == ["Đỗ Hải Yến", "Ngô Tuấn Kiệt", "Lương Bảo Trâm"]
    assert recruiter_client.get(CANDIDATES, {"ordering": "email_hash"}).status_code == 400


def test_submitted_count_reflects_applications(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile, full_name="Phạm Minh Quân")
    ApplicationFactory(candidate=candidate, recruiter=recruiter_profile)

    assert recruiter_client.get(CANDIDATES).json()["results"][0]["submitted_count"] == 1


# ── create ───────────────────────────────────────────────────────────────────


def test_creating_a_candidate_stores_the_owner_and_the_hashes(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    response = recruiter_client.post(
        CANDIDATES,
        {
            "full_name": "Trương Mỹ Hạnh",
            "current_title": "Payments Backend Lead",
            "email": "myhanh.truong@gmail.com",
            "phone": "+84909172846",
            "dob": "1991-06-14",
            "location": "hcm",
            "is_expat": False,
            "languages": ["Vietnamese", "English"],
            "skills": ["Go", "Kafka"],
            "years_experience": 9,
            "expected_salary": "62000000",
            "notes": "Prefers hybrid in District 4.",
        },
        format="json",
    )

    assert response.status_code == 201
    candidate = Candidate.objects.get(pk=response.json()["id"])
    assert candidate.owner == recruiter_profile
    assert candidate.email_hash != ""
    assert candidate.skills == ["Go", "Kafka"]


def test_a_new_candidate_is_searchable_straight_away(recruiter_client: APIClient) -> None:
    recruiter_client.post(
        CANDIDATES,
        {"full_name": "Trương Mỹ Hạnh", "current_title": "Payments Backend Lead"},
        format="json",
    )

    assert names(recruiter_client.get(f'{CANDIDATES}?q="Payments"')) == ["Trương Mỹ Hạnh"]


def test_a_name_is_required(recruiter_client: APIClient) -> None:
    response = recruiter_client.post(CANDIDATES, {"current_title": "Engineer"}, format="json")

    assert response.status_code == 400
    assert response.json()["full_name"] == ["Enter the full name"]


def test_more_than_twenty_skills_is_rejected(recruiter_client: APIClient) -> None:
    response = recruiter_client.post(
        CANDIDATES,
        {"full_name": "Trương Mỹ Hạnh", "skills": [f"Skill{index}" for index in range(21)]},
        format="json",
    )

    assert response.status_code == 400
    assert "20" in str(response.json()["skills"])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("years_experience", 61, "Up to 60 years."),
        ("current_salary", "-1", "Salary can't be negative."),
        ("expected_salary", "-1", "Salary can't be negative."),
    ],
)
def test_a_number_out_of_range_is_rejected(
    recruiter_client: APIClient, field: str, value: object, message: str
) -> None:
    response = recruiter_client.post(
        CANDIDATES, {"full_name": "Trương Mỹ Hạnh", field: value}, format="json"
    )

    assert response.status_code == 400
    assert response.json()[field] == [message]


@pytest.mark.parametrize("years_ago", [15, 82])
def test_a_date_of_birth_outside_working_age_is_rejected(
    recruiter_client: APIClient, years_ago: int
) -> None:
    born = date(timezone.localdate().year - years_ago, 1, 1)

    response = recruiter_client.post(
        CANDIDATES, {"full_name": "Trương Mỹ Hạnh", "dob": born.isoformat()}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["dob"] == ["Enter a date of birth for someone aged 16 to 80."]


def test_the_edges_of_every_range_are_accepted(recruiter_client: APIClient) -> None:
    response = recruiter_client.post(
        CANDIDATES,
        {
            "full_name": "Trương Mỹ Hạnh",
            "dob": date(timezone.localdate().year - 30, 1, 1).isoformat(),
            "years_experience": 60,
            "current_salary": "0",
            "expected_salary": "0",
        },
        format="json",
    )

    assert response.status_code == 201


def test_the_owner_cannot_be_forged(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    other = CandidateFactory().owner

    response = recruiter_client.post(
        CANDIDATES, {"full_name": "Trương Mỹ Hạnh", "owner": str(other.pk)}, format="json"
    )

    assert Candidate.objects.get(pk=response.json()["id"]).owner == recruiter_profile


# ── retrieve, update, delete ─────────────────────────────────────────────────


def test_the_owner_can_read_one_candidate(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile, full_name="Trương Mỹ Hạnh")

    body = recruiter_client.get(f"{CANDIDATES}/{candidate.pk}").json()

    assert body["full_name"] == "Trương Mỹ Hạnh"


def test_another_recruiters_candidate_is_404(recruiter_client: APIClient) -> None:
    someone_elses = CandidateFactory()

    assert recruiter_client.get(f"{CANDIDATES}/{someone_elses.pk}").status_code == 404
    assert (
        recruiter_client.patch(
            f"{CANDIDATES}/{someone_elses.pk}", {"notes": "x"}, format="json"
        ).status_code
        == 404
    )
    assert recruiter_client.delete(f"{CANDIDATES}/{someone_elses.pk}").status_code == 404


def test_patching_rewrites_the_search_vector(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile, skills=["React"])

    response = recruiter_client.patch(
        f"{CANDIDATES}/{candidate.pk}", {"skills": ["Kotlin"]}, format="json"
    )

    assert response.status_code == 200
    assert names(recruiter_client.get(f'{CANDIDATES}?q="Kotlin"')) == [candidate.full_name]
    assert names(recruiter_client.get(f'{CANDIDATES}?q="React"')) == []


def test_deleting_an_unused_candidate_works(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile)

    assert recruiter_client.delete(f"{CANDIDATES}/{candidate.pk}").status_code == 204
    assert not Candidate.objects.filter(pk=candidate.pk).exists()


def test_deleting_a_submitted_candidate_is_409(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile)
    ApplicationFactory(candidate=candidate, recruiter=recruiter_profile)

    response = recruiter_client.delete(f"{CANDIDATES}/{candidate.pk}")

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "This candidate has already been submitted to a job and cannot be deleted."
    )
    assert Candidate.objects.filter(pk=candidate.pk).exists()


# ── permissions ──────────────────────────────────────────────────────────────


def test_a_company_user_sees_nothing_here(client_for: ClientFor, company_ta: User) -> None:
    response = client_for(company_ta).get(CANDIDATES)

    assert response.status_code == 403
    assert response.json()["detail"] == "This area is for recruiter accounts."


def test_a_pending_recruiter_is_blocked(
    client_for: ClientFor, pending_recruiter_user: User
) -> None:
    response = client_for(pending_recruiter_user).get(CANDIDATES)

    assert response.status_code == 403
    assert response.json()["detail"] == "Your recruiter profile is waiting for review."


def test_anonymous_is_rejected(api_client: APIClient) -> None:
    assert api_client.get(CANDIDATES).status_code == 403
