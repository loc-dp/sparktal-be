from collections.abc import Callable
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import Company
from apps.jobs.access import grant_creator_perms
from apps.jobs.models import Job, JobStatus
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db

URL = "/api/v1/jobs"

ClientFor = Callable[[User], APIClient]


def payload(company: Company, **overrides: Any) -> dict[str, Any]:
    office = company.offices.first()
    assert office is not None
    data: dict[str, Any] = {
        "title": "Head of Finance",
        "why_we_need_you": "We are preparing for Series C and need an owner for FP&A.",
        "roles": "Own FP&A and the board pack\nLead treasury",
        "qualifications": "10+ years in finance\nIFRS and VAS",
        "nice_to_have": "",
        "benefits": "13th-month salary",
        "salary_min": "90000000",
        "salary_max": "120000000",
        "pay_basis": "gross",
        "work_model": "hybrid",
        "office": str(office.id),
        "bounty_type": "pct",
        "bounty_pct": 20,
        "skills": ["FP&A", "IFRS", "Treasury"],
        "role_type": "new_position",
        "working_hours": "40h_mon_fri",
        "languages": ["vi", "en"],
    }
    data.update(overrides)
    return data


def test_ta_can_create_a_pending_job_with_inferred_function(
    client_for: ClientFor, company_ta: User, company: Company
) -> None:
    response = client_for(company_ta).post(URL, payload(company), format="json")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["function"] == "finance"
    assert body["share_token"]
    job = Job.objects.get(id=body["id"])
    assert job.created_by == company_ta
    assert job.published_at is None


@pytest.mark.parametrize("field", ["role_type", "working_hours", "languages"])
def test_a_new_job_must_answer_the_required_brief_questions(
    client_for: ClientFor, company_ta: User, company: Company, field: str
) -> None:
    data = payload(company)
    del data[field]

    response = client_for(company_ta).post(URL, data, format="json")

    assert response.status_code == 400
    assert field in response.json()


def test_the_whole_brief_is_stored_and_returned(
    client_for: ClientFor, company_ta: User, company: Company
) -> None:
    brief = {
        "reporting_line": "Reports to the CEO\nManages 4 accountants",
        "has_internal_candidate": True,
        "search_duration": "over_1_month",
        "target_start_date": "2026-12-01",
        "interview_rounds_count": 3,
        "interview_process_details": "HR screen\nCase study\nCEO",
        "core_competencies": "• Integrity\n• Ownership",
        "probation_expectations": "Close the Q4 books on time",
        "salary_negotiable": False,
        "allowances": "- Lunch 1.000.000 ₫\n- 13th-month salary",
        "languages": ["en", "vi", "en"],
    }

    response = client_for(company_ta).post(URL, payload(company, **brief), format="json")

    assert response.status_code == 201
    body = response.json()
    assert body["core_competencies"] == "Integrity\nOwnership"
    assert body["allowances"] == "Lunch 1.000.000 ₫\n13th-month salary"
    assert body["languages"] == ["en", "vi"]
    for field in (
        "reporting_line",
        "has_internal_candidate",
        "search_duration",
        "target_start_date",
        "interview_rounds_count",
        "salary_negotiable",
    ):
        assert body[field] == brief[field]


def test_a_picked_function_beats_the_guess_and_survives_a_new_title(
    client_for: ClientFor, company_ta: User, company: Company
) -> None:
    client = client_for(company_ta)
    created = client.post(URL, payload(company, function="operations"), format="json").json()

    assert created["function"] == "operations"
    patched = client.patch(
        f"{URL}/{created['id']}",
        {"title": "Senior Backend Engineer", "skills": ["Go"]},
        format="json",
    ).json()
    assert patched["function"] == "operations"


def test_a_guessed_function_follows_a_new_title(
    client_for: ClientFor, company_ta: User, company: Company
) -> None:
    client = client_for(company_ta)
    created = client.post(URL, payload(company), format="json").json()

    patched = client.patch(
        f"{URL}/{created['id']}",
        {"title": "Senior Backend Engineer", "skills": ["Go"]},
        format="json",
    ).json()

    assert patched["function"] == "engineering"


def test_jobs_posted_before_the_brief_still_take_a_partial_edit(
    client_for: ClientFor, company_ta: User, company: Company
) -> None:
    job = JobFactory(company=company)  # No brief answers, like every job before it.

    response = client_for(company_ta).patch(
        f"{URL}/{job.id}", {"note_for_recruiters": "Prefer fintech"}, format="json"
    )

    assert response.status_code == 200


def test_creating_grants_the_creator_object_permissions(
    client_for: ClientFor, company_hm: User, company: Company
) -> None:
    response = client_for(company_hm).post(URL, payload(company), format="json")

    job = Job.objects.get(id=response.json()["id"])
    assert company_hm.has_perm("jobs.view_job", job)
    assert company_hm.has_perm("jobs.change_job", job)


def test_recruiter_cannot_create(
    client_for: ClientFor, recruiter_user: User, company: Company
) -> None:
    assert client_for(recruiter_user).post(URL, payload(company), format="json").status_code == 403


@pytest.mark.parametrize(
    ("overrides", "field", "message"),
    [
        ({"title": ""}, "title", "Enter a job title"),
        ({"why_we_need_you": ""}, "why_we_need_you", "Tell recruiters why this role exists"),
        ({"roles": ""}, "roles", "List the main responsibilities"),
        ({"qualifications": ""}, "qualifications", "Add the must-have qualifications"),
        (
            {"salary_min": "80000000", "salary_max": "50000000"},
            "salary_max",
            "Max salary must be at least the min",
        ),
        (
            {"bounty_type": "fixed", "bounty_amount": None},
            "bounty_amount",
            "Enter the fixed bounty amount",
        ),
        ({"bounty_type": "pct", "bounty_pct": 40}, "bounty_pct", "between 5 and 30"),
        ({"work_model": "onsite", "office": None}, "office", "Select an office for this job"),
        ({"role_type": ""}, "role_type", "new position or a replacement"),
        ({"working_hours": ""}, "working_hours", "Select the working hours"),
        ({"languages": []}, "languages", "Select at least one language"),
        ({"languages": ["ko"]}, "languages", "is not a valid choice"),
        ({"interview_rounds_count": 11}, "interview_rounds_count", "less than or equal to 10"),
    ],
)
def test_validation_messages(
    client_for: ClientFor,
    company_ta: User,
    company: Company,
    overrides: dict[str, Any],
    field: str,
    message: str,
) -> None:
    response = client_for(company_ta).post(URL, payload(company, **overrides), format="json")

    assert response.status_code == 400
    assert message in str(response.json()[field])


def test_remote_jobs_do_not_need_an_office(
    client_for: ClientFor, company_ta: User, company: Company
) -> None:
    response = client_for(company_ta).post(
        URL, payload(company, work_model="remote", office=None), format="json"
    )

    assert response.status_code == 201
    assert response.json()["location_label"] == "Remote (Vietnam)"


def test_office_of_another_company_is_rejected(
    client_for: ClientFor, company_ta: User, company: Company, other_company_admin: User
) -> None:
    other_office = other_company_admin.membership.company.offices.create(
        address="1 Somewhere", city="hn", position=0
    )

    response = client_for(company_ta).post(
        URL, payload(company, office=str(other_office.id)), format="json"
    )

    assert response.status_code == 400
    assert "office" in response.json()


def test_skills_are_capped_and_deduplicated(
    client_for: ClientFor, company_ta: User, company: Company
) -> None:
    response = client_for(company_ta).post(
        URL, payload(company, skills=["Go", "go", " Go "]), format="json"
    )

    assert response.json()["skills"] == ["Go"]

    too_many = client_for(company_ta).post(
        URL, payload(company, skills=[f"S{index}" for index in range(21)]), format="json"
    )

    assert too_many.status_code == 400
    assert "Up to 20 skills" in str(too_many.json()["skills"])


def test_bullets_are_stripped_from_multiline_fields(
    client_for: ClientFor, company_ta: User, company: Company
) -> None:
    response = client_for(company_ta).post(
        URL,
        payload(company, roles="• Own FP&A\n- Lead treasury\n\n  * Coach the team"),
        format="json",
    )

    assert response.json()["roles"] == "Own FP&A\nLead treasury\nCoach the team"


def test_patch_updates_a_job_and_keeps_its_status(
    client_for: ClientFor, company_ta: User, company: Company
) -> None:
    job = JobFactory(company=company, status=JobStatus.ACTIVE)

    response = client_for(company_ta).patch(
        f"{URL}/{job.id}", {"title": "Staff Backend Engineer"}, format="json"
    )

    assert response.status_code == 200
    job.refresh_from_db()
    assert job.title == "Staff Backend Engineer"
    assert job.status == JobStatus.ACTIVE


def test_hiring_manager_cannot_touch_someone_elses_job(
    client_for: ClientFor, company_hm: User, company: Company
) -> None:
    job = JobFactory(company=company)

    client = client_for(company_hm)
    assert client.get(f"{URL}/{job.id}").status_code == 404
    assert client.patch(f"{URL}/{job.id}", {"title": "x"}, format="json").status_code == 404


def test_hiring_manager_can_edit_a_granted_job(
    client_for: ClientFor, company_hm: User, company: Company
) -> None:
    job = JobFactory(company=company, created_by=company_hm)
    grant_creator_perms(job, company_hm)

    response = client_for(company_hm).patch(f"{URL}/{job.id}", {"title": "x"}, format="json")

    assert response.status_code == 200


def test_job_of_another_company_is_404(
    client_for: ClientFor, other_company_admin: User, company: Company
) -> None:
    job = JobFactory(company=company)

    assert client_for(other_company_admin).get(f"{URL}/{job.id}").status_code == 404


def test_public_share_endpoint_shows_active_jobs_without_bounty(
    api_client: APIClient, company: Company
) -> None:
    job = JobFactory(company=company, status=JobStatus.ACTIVE, note_for_recruiters="internal")

    response = api_client.get(f"/api/v1/public/jobs/{job.share_token}")

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == job.title
    assert body["company"]["name"] == company.name
    assert "bounty_pct" not in body
    assert "note_for_recruiters" not in body


def test_public_share_endpoint_keeps_the_companys_search_private(
    api_client: APIClient, company: Company
) -> None:
    job = JobFactory(
        company=company,
        status=JobStatus.ACTIVE,
        working_hours="40h_mon_fri",
        languages=["vi"],
        has_internal_candidate=True,
        role_type="replacement",
    )

    body = api_client.get(f"/api/v1/public/jobs/{job.share_token}").json()

    assert body["working_hours"] == "40h_mon_fri"
    assert body["languages"] == ["vi"]
    for private in (
        "has_internal_candidate",
        "role_type",
        "search_duration",
        "reporting_line",
        "target_start_date",
        "probation_expectations",
    ):
        assert private not in body


def test_public_share_endpoint_hides_pending_and_closed_jobs(
    api_client: APIClient, company: Company
) -> None:
    pending = JobFactory(company=company, status=JobStatus.PENDING)
    closed = JobFactory(company=company, status=JobStatus.CLOSED)

    assert api_client.get(f"/api/v1/public/jobs/{pending.share_token}").status_code == 404
    assert api_client.get(f"/api/v1/public/jobs/{closed.share_token}").status_code == 404
