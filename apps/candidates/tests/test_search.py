import pytest
from django.core.exceptions import ValidationError

from apps.candidates.models import Candidate
from apps.candidates.search import MAX_TOKENS, lex, parse_boolean
from apps.candidates.tests.factories import CandidateFactory
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db

PRODUCT_EXAMPLE = '("Frontend" OR "Fullstack") AND "React" NOT "Junior"'


@pytest.fixture
def pool(recruiter_profile: RecruiterProfile) -> None:
    CandidateFactory(
        owner=recruiter_profile,
        full_name="Đỗ Hải Yến",
        current_title="Frontend Engineer",
        skills=["React", "TypeScript"],
    )
    CandidateFactory(
        owner=recruiter_profile,
        full_name="Ngô Tuấn Kiệt",
        current_title="Fullstack Developer",
        skills=["React", "Node"],
    )
    CandidateFactory(
        owner=recruiter_profile,
        full_name="Lương Bảo Trâm",
        current_title="Junior Frontend Developer",
        skills=["React"],
    )
    CandidateFactory(
        owner=recruiter_profile,
        full_name="Hồ Quốc Duy",
        current_title="Backend Engineer",
        skills=["Go", "Kafka"],
    )


def matches(query: str) -> list[str]:
    parsed = parse_boolean(query)
    rows = (
        Candidate.objects.all()
        if parsed is None
        else Candidate.objects.filter(search_vector=parsed)
    )
    return sorted(row.full_name for row in rows)


def test_an_empty_query_means_no_filter() -> None:
    assert parse_boolean("") is None
    assert parse_boolean("    ") is None


def test_a_single_word_matches_one_column(pool: None) -> None:
    assert matches("Kafka") == ["Hồ Quốc Duy"]


def test_two_words_side_by_side_are_an_implicit_and(pool: None) -> None:
    assert matches("Frontend React") == ["Lương Bảo Trâm", "Đỗ Hải Yến"]


def test_explicit_and_behaves_the_same(pool: None) -> None:
    assert matches('"Frontend" AND "React"') == matches("Frontend React")


def test_or_widens_the_result(pool: None) -> None:
    assert matches('"Kafka" OR "TypeScript"') == ["Hồ Quốc Duy", "Đỗ Hải Yến"]


def test_not_removes_a_term(pool: None) -> None:
    assert matches('"React" NOT "Junior"') == ["Ngô Tuấn Kiệt", "Đỗ Hải Yến"]


def test_parentheses_group_an_or(pool: None) -> None:
    assert matches('("Backend" OR "Fullstack") AND "React"') == ["Ngô Tuấn Kiệt"]


def test_the_product_md_example(pool: None) -> None:
    assert matches(PRODUCT_EXAMPLE) == ["Ngô Tuấn Kiệt", "Đỗ Hải Yến"]


def test_a_quoted_phrase_matches_adjacent_words_only(pool: None) -> None:
    assert matches('"Frontend Engineer"') == ["Đỗ Hải Yến"]
    assert matches('"Engineer Frontend"') == []


def test_operators_are_case_sensitive_keywords(pool: None) -> None:
    # Lowercase `and` is an ordinary word, not an operator, so nothing matches.
    assert matches("Kafka and Go") == []


def test_nesting_survives(pool: None) -> None:
    assert matches('(("React" OR "Kafka") AND NOT "Junior") AND "Engineer"') == [
        "Hồ Quốc Duy",
        "Đỗ Hải Yến",
    ]


@pytest.mark.parametrize(
    "query",
    [
        'AND "React"',
        '"React" AND',
        '"React" OR',
        "NOT",
        '("React"',
        '"React")',
        "()",
        '"React',
        '"React" AND AND "Go"',
        "( )",
    ],
)
def test_broken_syntax_is_rejected(query: str) -> None:
    with pytest.raises(ValidationError, match=r"Check the Boolean syntax\."):
        parse_boolean(query)


def test_a_query_longer_than_twelve_tokens_is_rejected() -> None:
    too_long = " OR ".join(f'"skill{index}"' for index in range(MAX_TOKENS))

    with pytest.raises(ValidationError, match=r"Check the Boolean syntax\."):
        parse_boolean(too_long)


def test_a_query_at_the_token_limit_is_allowed() -> None:
    at_limit = '"a" OR "b" OR "c" OR "d" OR "e" OR NOT "f"'  # 6 phrases + 5 OR + 1 NOT

    assert len(lex(at_limit)) == MAX_TOKENS
    assert parse_boolean(at_limit) is not None
