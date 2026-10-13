import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.jobs.jd_fields import OfficeRef, normalize_jd

HCM_HQ = OfficeRef(id=str(uuid.uuid4()), city="hcm")
HANOI = OfficeRef(id=str(uuid.uuid4()), city="hn")
HANOI_2 = OfficeRef(id=str(uuid.uuid4()), city="hn")
OFFICES = [HCM_HQ, HANOI, HANOI_2]
SINGAPORE = OfficeRef(id=str(uuid.uuid4()), city="abroad", place="Singapore")
TOKYO = OfficeRef(id=str(uuid.uuid4()), city="abroad", place="Tokyo, Japan")


def raw(**overrides: Any) -> dict[str, Any]:
    return {
        "is_job_description": True,
        "title": "Senior Backend Engineer",
        "why_we_need_you": "Our payout volume tripled.",
        "why_we_need_you_drafted": False,
        "roles": ["Own the payout service"],
        "qualifications": ["5+ years with Go"],
        "nice_to_have": [],
        "benefits": [],
        "salary": None,
        "pay_basis": None,
        "work_model": None,
        "location": None,
        "city": None,
        "skills": [],
        "function": None,
        "reporting_line": [],
        "role_type": None,
        "core_competencies": [],
        "languages": [],
        "probation_expectations": [],
        "interview_rounds_count": None,
        "interview_process_details": [],
        "target_start_date": None,
        "allowances": [],
        "salary_negotiable": None,
        "working_hours": None,
        **overrides,
    }


def salary(
    minimum: Any, maximum: Any, currency: Any = "VND", period: Any = "month"
) -> dict[str, Any]:
    return {"minimum": minimum, "maximum": maximum, "currency": currency, "period": period}


def test_the_full_contract_comes_back_even_from_nothing() -> None:
    assert normalize_jd({}, []) == {
        "title": "",
        "function": "",
        "why_we_need_you": "",
        "why_we_need_you_drafted": False,
        "roles": "",
        "qualifications": "",
        "nice_to_have": "",
        "core_competencies": "",
        "benefits": "",
        "allowances": "",
        "reporting_line": "",
        "probation_expectations": "",
        "interview_process_details": "",
        "salary_min": None,
        "salary_max": None,
        "pay_basis": "",
        "work_model": "",
        "office": "",
        "unmatched_city": "",
        "skills": [],
        "working_hours": "",
        "role_type": "",
        "languages": [],
        "salary_negotiable": None,
        "interview_rounds_count": None,
        "target_start_date": "",
    }


def test_what_no_jd_says_is_never_filled() -> None:
    fields = normalize_jd(
        raw(
            bounty_pct=20,
            bounty_amount=5_000_000,
            has_internal_candidate=True,
            search_duration="over_3_months",
            note_for_recruiters="secret",
        ),
        OFFICES,
    )

    assert (
        not {
            "bounty_type",
            "bounty_pct",
            "bounty_amount",
            "has_internal_candidate",
            "search_duration",
            "note_for_recruiters",
        }
        & fields.keys()
    )


def test_claudes_function_wins_and_the_title_guess_backs_it_up() -> None:
    assert normalize_jd(raw(function="operations"), [])["function"] == "operations"
    assert normalize_jd(raw(function="hr"), [])["function"] == "engineering"
    assert normalize_jd(raw(title=None, roles=[]), [])["function"] == ""


def test_an_allowance_is_dropped_from_benefits_and_a_trait_from_qualifications() -> None:
    fields = normalize_jd(
        raw(
            benefits=["Bảo hiểm sức khỏe", "Thưởng tháng 13"],
            allowances=["Thưởng tháng 13", "Phụ cấp ăn trưa"],
            qualifications=["5+ years with Go", "Trung thực"],
            core_competencies=["• Trung thực"],
        ),
        [],
    )

    assert fields["benefits"] == "Bảo hiểm sức khỏe"
    assert fields["allowances"] == "Thưởng tháng 13\nPhụ cấp ăn trưa"
    assert fields["qualifications"] == "5+ years with Go"
    assert fields["core_competencies"] == "Trung thực"


def test_a_duty_about_reporting_stays_a_duty() -> None:
    # Claude once copied a duty into reporting_line too; losing it from roles costs a hire.
    duty = "Report issues to the Store Design Manager"
    fields = normalize_jd(raw(roles=[duty], reporting_line=[duty]), [])

    assert fields["roles"] == duty


def test_brief_choices_keep_only_known_codes() -> None:
    fields = normalize_jd(
        raw(
            working_hours="45h",
            role_type="replacement",
            languages=["vi", "ko", "en", "vi", 3],
            salary_negotiable="yes",
            interview_rounds_count=True,
        ),
        [],
    )

    assert fields["working_hours"] == ""
    assert fields["role_type"] == "replacement"
    assert fields["languages"] == ["vi", "en"]
    assert fields["salary_negotiable"] is None
    assert fields["interview_rounds_count"] is None


@pytest.mark.parametrize(("rounds", "kept"), [(0, None), (1, 1), (4, 4), (10, 10), (11, None)])
def test_interview_rounds_stay_in_range(rounds: int, kept: int | None) -> None:
    assert normalize_jd(raw(interview_rounds_count=rounds), [])["interview_rounds_count"] == kept


def test_only_a_start_date_still_ahead_is_kept() -> None:
    today = timezone.localdate()
    ahead = (today + timedelta(days=30)).isoformat()
    passed = (today - timedelta(days=1)).isoformat()

    assert normalize_jd(raw(target_start_date=ahead), [])["target_start_date"] == ahead
    assert normalize_jd(raw(target_start_date=passed), [])["target_start_date"] == ""
    assert normalize_jd(raw(target_start_date="ASAP"), [])["target_start_date"] == ""


def test_the_title_is_squeezed_and_capped_at_a_word_boundary() -> None:
    long_title = "Senior " + "Principal " * 20 + "Engineer"

    assert normalize_jd(raw(title="  Kế  toán\ntrưởng "), [])["title"] == "Kế toán trưởng"
    capped = normalize_jd(raw(title=long_title), [])["title"]
    assert len(capped) <= 120
    assert capped.endswith("Principal")


def test_list_items_become_clean_lines() -> None:
    fields = normalize_jd(
        raw(
            roles=[
                "• Own the payout service",
                "  - Review   designs ",
                "",
                "1. Mentor two engineers;",
                "Báo cáo tiến độ cho nhân viên .",
            ],
            qualifications=[
                "* 5+ years with Go",
                "5+ YEARS WITH GO",
                "✓ Postgres",
                "1.5 years in fintech",
            ],
            benefits=["Lương tháng 13\nBảo hiểm PVI", 42],
        ),
        [],
    )

    assert fields["roles"] == (
        "Own the payout service\nReview designs\nMentor two engineers\n"
        "Báo cáo tiến độ cho nhân viên."
    )
    assert fields["qualifications"] == "5+ years with Go\nPostgres\n1.5 years in fintech"
    assert fields["benefits"] == "Lương tháng 13\nBảo hiểm PVI"


def test_a_nice_to_have_repeated_in_qualifications_stays_only_in_nice_to_have() -> None:
    fields = normalize_jd(
        raw(qualifications=["Go", "Kafka is a plus"], nice_to_have=["Kafka is a plus"]), []
    )

    assert fields["qualifications"] == "Go"
    assert fields["nice_to_have"] == "Kafka is a plus"


def test_why_we_need_you_keeps_paragraphs_and_its_draft_flag() -> None:
    fields = normalize_jd(
        raw(why_we_need_you="Line one.\n\n  Line   two. ", why_we_need_you_drafted=True), []
    )

    assert fields["why_we_need_you"] == "Line one.\nLine two."
    assert fields["why_we_need_you_drafted"] is True


JD_TEXT = """Senior Backend Engineer
About the role:
Our payout volume  tripled this year, and the
team that runs it needs a second senior engineer.
Responsibilities
- Own the payout service
- Review designs"""


def test_a_paragraph_found_in_the_jd_is_not_flagged_whatever_claude_says() -> None:
    # Line wraps, case and punctuation differ from the file; the words are the JD's.
    why = (
        "Our payout volume tripled this year, and the team that runs it needs a second "
        "SENIOR engineer"
    )
    fields = normalize_jd(
        raw(why_we_need_you=why, why_we_need_you_drafted=True, roles=["• Own the payout service"]),
        [],
        JD_TEXT,
    )

    assert fields["why_we_need_you_drafted"] is False


def test_reworded_text_is_flagged_even_when_claude_calls_it_copied() -> None:
    why = "Our payout volume tripled this year. This hire will own the payout service."
    fields = normalize_jd(
        raw(why_we_need_you=why, why_we_need_you_drafted=False, roles=["Own the payout service"]),
        [],
        JD_TEXT,
    )

    assert fields["why_we_need_you_drafted"] is True


@pytest.mark.parametrize("source", ["", "%PDF garbage the text layer could not read"])
def test_claudes_flag_stands_when_the_jd_text_cannot_be_checked(source: str) -> None:
    fields = normalize_jd(raw(why_we_need_you_drafted=True), [], source)

    assert fields["why_we_need_you_drafted"] is True


def test_an_empty_draft_is_not_flagged() -> None:
    fields = normalize_jd(raw(why_we_need_you=None, why_we_need_you_drafted=True), [])

    assert fields["why_we_need_you_drafted"] is False


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (salary(15_000_000, 20_000_000), (15_000_000, 20_000_000)),
        (salary(None, 40_000_000), (None, 40_000_000)),
        (salary(25_000_000, None, currency="vnđ", period=None), (25_000_000, None)),
        (salary(15_000_000, 20_000_000, currency="₫"), (15_000_000, 20_000_000)),
        (salary(300_000_000, 400_000_000, period="year"), (25_000_000, 33_300_000)),
    ],
    ids=("range", "up-to", "no-period-means-monthly", "dong-sign", "yearly-as-monthly"),
)
def test_a_monthly_vnd_salary_is_kept(
    given: dict[str, Any], expected: tuple[int | None, int | None]
) -> None:
    fields = normalize_jd(raw(salary=given), [])

    assert (fields["salary_min"], fields["salary_max"]) == expected


@pytest.mark.parametrize(
    "given",
    [
        salary(1500, 2500, currency="USD"),
        salary(15_000_000, 20_000_000, period="week"),
        salary(15, 20),
        salary(20_000_000, 15_000_000),
        salary("lots", None),
        salary(True, None),
        salary(-1, 20_000_000),
        salary(10**13, None),
        None,
    ],
    ids=(
        "usd",
        "weekly",
        "millions-not-expanded",
        "max-below-min",
        "text",
        "bool",
        "negative",
        "absurd",
        "negotiable",
    ),
)
def test_any_other_salary_is_left_blank(given: Any) -> None:
    fields = normalize_jd(raw(salary=given), [])

    assert (fields["salary_min"], fields["salary_max"]) == (None, None)


def test_pay_basis_and_work_model_only_from_their_choices() -> None:
    assert normalize_jd(raw(pay_basis="net", work_model="hybrid"), [])["pay_basis"] == "net"
    assert normalize_jd(raw(work_model="hybrid"), [])["work_model"] == "hybrid"
    junk = normalize_jd(raw(pay_basis="after tax", work_model="wfh"), [])
    assert (junk["pay_basis"], junk["work_model"]) == ("", "")


def test_a_city_with_exactly_one_office_picks_it() -> None:
    assert normalize_jd(raw(city="hcm"), OFFICES)["office"] == HCM_HQ.id


@pytest.mark.parametrize("city", ["hn", "dn", "other", "atlantis", None])
def test_a_city_with_zero_or_several_offices_leaves_the_office_alone(city: Any) -> None:
    assert normalize_jd(raw(city=city), OFFICES)["office"] == ""


def test_a_remote_job_has_no_office() -> None:
    assert normalize_jd(raw(city="hcm", work_model="remote"), OFFICES)["office"] == ""


def test_skills_are_capped_trimmed_and_deduplicated() -> None:
    many = [f"Skill {n}" for n in range(30)]
    fields = normalize_jd(raw(skills=["Go", " go ", "x" * 41, "", 7, *many]), [])

    assert fields["skills"][0] == "Go"
    assert len(fields["skills"]) == 20
    assert all(len(skill) <= 40 for skill in fields["skills"])
    assert [s.lower() for s in fields["skills"]].count("go") == 1


def test_not_a_job_description_reads_as_empty() -> None:
    assert normalize_jd(raw(is_job_description=False), OFFICES)["title"] == ""


def test_a_city_without_a_single_office_is_named_for_the_form_to_flag() -> None:
    assert normalize_jd(raw(city="dn"), OFFICES)["unmatched_city"] == "Da Nang"
    assert normalize_jd(raw(city="hn"), OFFICES)["unmatched_city"] == "Ha Noi"
    assert normalize_jd(raw(city="hcm"), OFFICES)["unmatched_city"] == ""
    assert normalize_jd(raw(city="dn", work_model="remote"), OFFICES)["unmatched_city"] == ""
    assert normalize_jd(raw(city="other"), OFFICES)["unmatched_city"] == ""
    assert normalize_jd(raw(city="atlantis"), OFFICES)["unmatched_city"] == ""


def test_a_place_without_a_city_code_is_named_as_written() -> None:
    assert normalize_jd(raw(location="  Vũng  Tàu "), OFFICES)["unmatched_city"] == "Vũng Tàu"
    several = normalize_jd(raw(location="Hà Nội, TP.HCM"), OFFICES)
    assert (several["office"], several["unmatched_city"]) == ("", "Hà Nội, TP.HCM")
    # A code still wins: its label is translated in the form, the JD's spelling is not.
    assert normalize_jd(raw(city="dn", location="Hải Châu"), OFFICES)["unmatched_city"] == "Da Nang"
    assert normalize_jd(raw(city="hcm", location="Quận 7"), OFFICES)["unmatched_city"] == ""
    remote = raw(location="Cần Giờ", work_model="remote")
    assert normalize_jd(remote, OFFICES)["unmatched_city"] == ""
    assert len(normalize_jd(raw(location="Khu " * 40), OFFICES)["unmatched_city"]) <= 80


@pytest.mark.parametrize(
    ("place", "office"),
    [
        ("Singapore", SINGAPORE),
        ("Shibuya, Tokyo", TOKYO),
        ("Japan", TOKYO),
        ("SINGAPORE", SINGAPORE),
    ],
)
def test_a_job_abroad_picks_the_office_in_that_place(place: str, office: OfficeRef) -> None:
    fields = normalize_jd(raw(city="abroad", location=place), [*OFFICES, SINGAPORE, TOKYO])

    assert (fields["office"], fields["unmatched_city"]) == (office.id, "")


def test_a_job_abroad_without_an_office_there_is_named_as_written() -> None:
    # Every office abroad shares one code, so the code alone never picks one.
    fields = normalize_jd(raw(city="abroad", location="Seoul, Korea"), [*OFFICES, SINGAPORE])

    assert (fields["office"], fields["unmatched_city"]) == ("", "Seoul, Korea")
    assert normalize_jd(raw(city="abroad"), [SINGAPORE])["office"] == ""
