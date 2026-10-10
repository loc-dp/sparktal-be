from datetime import date

from django.utils import timezone

from apps.candidates.cv_fields import fill_contacts, normalize_fields

SOURCE = """Truong My Hanh
Email: myhanh.truong@gmail.com
Phone: 0909 172 846
"""

CONTRACT = {
    "full_name",
    "current_title",
    "email",
    "phone",
    "dob",
    "location",
    "is_expat",
    "languages",
    "skills",
    "years_experience",
    "current_salary",
    "expected_salary",
}


def years_ago(years: int) -> str:
    return date(timezone.localdate().year - years, 1, 1).isoformat()


def test_an_empty_result_still_returns_every_contract_key() -> None:
    fields = normalize_fields({})

    assert set(fields) == CONTRACT
    assert fields["full_name"] == ""
    assert fields["dob"] is None
    assert fields["is_expat"] is False
    assert fields["skills"] == []
    assert fields["years_experience"] is None


def test_a_clean_result_passes_through() -> None:
    fields = normalize_fields(
        {
            "full_name": "Trương Mỹ Hạnh",
            "current_title": "Payments Backend Lead",
            "email": "MyHanh.Truong@gmail.com",
            "phone": "0909 172 846",
            "dob": years_ago(30),
            "location": "hcm",
            "is_expat": False,
            "languages": ["Vietnamese", "English"],
            "skills": ["Go", "Kafka"],
            "years_experience": 9,
            "current_salary": 45000000,
            "expected_salary": "55000000",
        },
        source_text=SOURCE,
    )

    assert fields["full_name"] == "Trương Mỹ Hạnh"
    assert fields["email"] == "myhanh.truong@gmail.com"
    assert fields["phone"] == "+84909172846"
    assert fields["dob"] == years_ago(30)
    assert fields["location"] == "hcm"
    assert fields["languages"] == ["Vietnamese", "English"]
    assert fields["years_experience"] == 9
    assert fields["current_salary"] == 45000000
    assert fields["expected_salary"] == 55000000


def test_a_shouted_vietnamese_name_is_title_cased_with_diacritics() -> None:
    assert normalize_fields({"full_name": "NGUYỄN   VĂN AN"})["full_name"] == "Nguyễn Văn An"


def test_values_beyond_the_column_limits_are_clamped_or_dropped() -> None:
    fields = normalize_fields(
        {
            "full_name": "A" * 300,
            "current_title": "x" * 300,
            "location": "Saigon",
            "is_expat": None,
            "languages": ["L" * 40, *[f"Lang {n}" for n in range(15)]],
            "skills": [f"Skill {n}" for n in range(30)] + ["s" * 80],
            "years_experience": 99,
            "current_salary": -5,
            "expected_salary": 10**16,
        }
    )

    assert len(fields["full_name"]) == 120
    assert len(fields["current_title"]) == 160
    assert fields["location"] == ""
    assert fields["is_expat"] is False
    assert len(fields["languages"]) == 10
    assert len(fields["languages"][0]) == 24
    assert len(fields["skills"]) == 20
    assert fields["years_experience"] is None
    assert fields["current_salary"] is None
    assert fields["expected_salary"] is None


def test_duplicate_tags_are_dropped_case_insensitively() -> None:
    fields = normalize_fields(
        {"skills": ["Go", "go ", "GO", "Kafka"], "languages": ["English", "english"]}
    )

    assert fields["skills"] == ["Go", "Kafka"]
    assert fields["languages"] == ["English"]


def test_wrong_types_are_dropped_not_crashed_on() -> None:
    fields = normalize_fields(
        {
            "full_name": 42,
            "skills": "Go, Kafka",
            "years_experience": True,
            "dob": 1991,
            "email": ["a@b.co"],
        }
    )

    assert fields["full_name"] == ""
    assert fields["skills"] == []
    assert fields["years_experience"] is None
    assert fields["dob"] is None
    assert fields["email"] == ""


def test_an_implausible_birth_date_is_dropped() -> None:
    assert normalize_fields({"dob": years_ago(10)})["dob"] is None
    assert normalize_fields({"dob": years_ago(95)})["dob"] is None
    assert normalize_fields({"dob": "14/06/1991"})["dob"] is None


def test_malformed_contacts_are_dropped() -> None:
    fields = normalize_fields({"email": "not-an-email", "phone": "12345"})

    assert fields["email"] == ""
    assert fields["phone"] == ""


def test_contacts_missing_from_the_text_layer_are_treated_as_invented() -> None:
    fields = normalize_fields(
        {"email": "someone.else@gmail.com", "phone": "0912 345 678"}, source_text=SOURCE
    )

    assert fields["email"] == ""
    assert fields["phone"] == ""


def test_without_a_text_layer_contacts_are_only_format_checked() -> None:
    fields = normalize_fields({"email": "hanh@gmail.com", "phone": "0909 172 846"}, source_text="")

    assert fields["email"] == "hanh@gmail.com"
    assert fields["phone"] == "+84909172846"


def test_regex_only_fills_contacts_claude_left_empty() -> None:
    claude = normalize_fields({"full_name": "Hạnh", "email": "hanh@gmail.com"})
    regex = normalize_fields(
        {"full_name": "Truong", "email": "other@gmail.com", "phone": "0909 172 846"}
    )

    merged = fill_contacts(claude, regex)

    assert merged["full_name"] == "Hạnh"
    assert merged["email"] == "hanh@gmail.com"
    assert merged["phone"] == "+84909172846"


def test_an_email_broken_across_lines_in_the_text_layer_is_still_found() -> None:
    wrapped = "Mobile +84 398 537 012\ntranvanchung290597@gmail.co\nm\nlinkedin"
    spaced = "E\nm\na\ni\nl\n:\nt\nr\nuong\nuy\ne\nnm\ni\nnhi\ne\nu@\ny\na\nh\noo.c\nom"

    assert normalize_fields({"email": "tranvanchung290597@gmail.com"}, wrapped)["email"] == (
        "tranvanchung290597@gmail.com"
    )
    assert normalize_fields({"email": "truonguyenminhieu@yahoo.com"}, spaced)["email"] == (
        "truonguyenminhieu@yahoo.com"
    )


def test_the_first_valid_contact_is_taken_from_a_list() -> None:
    source = "quanghue.esg@outlook.com / quanghue.eohs@gmail.com\n0909 172 846 / 0912 345 678"
    fields = normalize_fields(
        {
            "email": "quanghue.ESG@outlook.com / quanghue.eohs@gmail.com",
            "phone": "0909 172 846 / 0912 345 678",
        },
        source,
    )

    assert fields["email"] == "quanghue.esg@outlook.com"
    assert fields["phone"] == "+84909172846"


def test_a_shouted_name_keeps_its_nickname_and_degree() -> None:
    assert normalize_fields({"full_name": "ĐỖ THỊ QUỲNH CHÂU (Vicky)"})["full_name"] == (
        "Đỗ Thị Quỳnh Châu (Vicky)"
    )
    assert normalize_fields({"full_name": "PHAM NGOC DINH, Ph.D."})["full_name"] == (
        "Pham Ngoc Dinh, Ph.D."
    )


def test_a_lone_acronym_in_a_name_is_left_alone() -> None:
    assert (
        normalize_fields({"full_name": "Nguyen Van An, MBA"})["full_name"] == "Nguyen Van An, MBA"
    )


def months_ago(months: int) -> str:
    today = timezone.localdate()
    index = today.year * 12 + today.month - 1 - months
    return f"{index // 12}-{index % 12 + 1:02d}"


def years_from(*jobs: tuple[str | None, str | None], stated: object = None) -> object:
    raw = {
        "jobs": [{"start": start, "end": end} for start, end in jobs],
        "years_experience": stated,
    }
    return normalize_fields(raw)["years_experience"]


def test_years_are_counted_from_job_dates() -> None:
    assert years_from(("2017-09", "2020-09"), ("2022-09", "2023-10")) == 4


def test_overlapping_jobs_are_counted_once() -> None:
    # Two concurrent roles (2018-2020 overlaps 2015-2020) must not add up to 7 years.
    assert years_from(("2015-01", "2020-01"), ("2018-01", "2020-01")) == 5


def test_a_current_job_runs_to_today() -> None:
    assert years_from((months_ago(5 * 12 + 5), "present")) == 5


def test_a_year_without_a_month_counts_from_january() -> None:
    # "2015 - 2017, 2017 - 2023" is eight years, not nine.
    assert years_from(("2015", "2017"), ("2017", "2023")) == 8


def test_job_dates_beat_a_stated_total() -> None:
    # "20 years of experience" in an old summary goes stale; the dated history does not.
    assert years_from(("2010-01", "2020-01"), stated=20) == 10


def test_a_stated_total_is_used_when_no_job_has_dates() -> None:
    assert years_from((None, None), stated=8) == 8
    assert normalize_fields({"years_experience": 8})["years_experience"] == 8


def test_impossible_job_dates_are_ignored() -> None:
    assert years_from(("2020-01", "2015-01"), ("garbage", "present"), stated=3) == 3
    assert years_from((months_ago(-24), "present"), stated=3) == 3
    assert years_from(("1890-01", "1900-01"), stated=3) == 3


def test_a_month_out_of_range_is_ignored() -> None:
    assert years_from(("2015-13", "2020-01"), stated=3) == 3


def test_an_end_month_is_worked_through() -> None:
    # "Jan 2010 - Dec 2014, Jan 2015 - Dec 2019" is ten full years with no gap between jobs.
    assert years_from(("2010-01", "2014-12"), ("2015-01", "2019-12")) == 10
