import base64
from types import SimpleNamespace
from typing import Any

import pytest
from django.conf import settings as django_settings

from apps.jobs import jd_parsing
from apps.jobs.jd_parsing import ParsedJd, instructions, is_enabled, llm_extract

PDF = b"%PDF-1.4 fake"
TEXT = "Senior Accountant\nResponsibilities\n- Close the books monthly"


def parsed(**overrides: Any) -> ParsedJd:
    return ParsedJd(
        **{
            "is_job_description": True,
            "title": "Senior Accountant",
            "why_we_need_you": None,
            "why_we_need_you_drafted": False,
            "roles": ["Close the books monthly"],
            "qualifications": [],
            "nice_to_have": [],
            "benefits": [],
            "salary": None,
            "pay_basis": None,
            "work_model": None,
            "location": None,
            "city": None,
            "skills": [],
            **overrides,
        }
    )


def fake_client(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def parse(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=parsed(),
            model="claude-sonnet-5-5-20261001",
            usage=SimpleNamespace(input_tokens=3100, output_tokens=640),
            _request_id="req_jd",
        )

    monkeypatch.setattr(
        jd_parsing, "_client", lambda: SimpleNamespace(messages=SimpleNamespace(parse=parse))
    )
    return calls


@pytest.fixture
def with_key(settings: Any) -> Any:
    settings.ANTHROPIC_API_KEY = "test-key"
    settings.JD_PARSE_MODEL = "claude-sonnet-5-5"
    return settings


def test_test_settings_keep_jd_parsing_off() -> None:
    assert django_settings.JD_PARSE_MODEL == ""
    assert not is_enabled()


def test_the_cv_model_alone_does_not_turn_jd_parsing_on(
    monkeypatch: pytest.MonkeyPatch, with_key: Any
) -> None:
    with_key.JD_PARSE_MODEL = ""
    with_key.CV_PARSE_MODEL = "claude-sonnet-5-5"
    calls = fake_client(monkeypatch)

    assert llm_extract(PDF, "jd.pdf", TEXT) is None
    assert calls == []


def test_a_key_without_a_model_never_calls_claude(
    monkeypatch: pytest.MonkeyPatch, with_key: Any
) -> None:
    with_key.ANTHROPIC_API_KEY = ""
    calls = fake_client(monkeypatch)

    assert llm_extract(PDF, "jd.pdf", TEXT) is None
    assert calls == []


def test_a_pdf_goes_to_the_jd_model_as_a_document(
    monkeypatch: pytest.MonkeyPatch, with_key: Any
) -> None:
    calls = fake_client(monkeypatch)

    read = llm_extract(PDF, "jd.pdf", TEXT)

    assert read is not None
    assert read.fields is not None
    assert read.fields["title"] == "Senior Accountant"
    assert (read.input_tokens, read.output_tokens) == (3100, 640)
    call = calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["output_format"] is ParsedJd
    document = call["messages"][0]["content"][0]
    assert base64.standard_b64decode(document["source"]["data"]) == PDF


def test_a_docx_goes_as_text_tagged_as_a_jd(monkeypatch: pytest.MonkeyPatch, with_key: Any) -> None:
    calls = fake_client(monkeypatch)

    llm_extract(b"PK", "jd.docx", TEXT)

    first = calls[0]["messages"][0]["content"][0]
    assert first == {"type": "text", "text": f"<jd>\n{TEXT}\n</jd>"}


def test_an_old_doc_is_never_sent(monkeypatch: pytest.MonkeyPatch, with_key: Any) -> None:
    calls = fake_client(monkeypatch)

    assert llm_extract(b"\xd0\xcf\x11\xe0", "jd.doc", "") is None
    assert calls == []


def test_the_prompt_keeps_the_jds_own_words() -> None:
    prompt = instructions()

    assert "verbatim" in prompt
    assert "Never translate" in prompt


def test_the_prompt_never_asks_for_bounty_or_function() -> None:
    prompt = instructions().lower()

    assert "bounty" not in prompt
    assert "function" not in ParsedJd.model_fields
    assert not any(name.startswith("bounty") for name in ParsedJd.model_fields)


def test_the_prompt_lists_city_codes_but_not_other() -> None:
    prompt = instructions()

    for code in ("hcm = ", "hn = ", "dn = "):
        assert code in prompt
    assert "other = " not in prompt


def test_the_prompt_maps_old_provinces_to_the_merged_ones() -> None:
    # A JD from before July 2025 still says Bình Dương; the office list only knows hcm.
    prompt = instructions()

    assert "Bình Dương" in prompt
    assert "bd = " not in prompt


def test_the_prompt_marks_a_drafted_why_we_need_you() -> None:
    prompt = instructions()

    assert "why_we_need_you_drafted true" in prompt
    assert "only facts and wording found in the JD" in prompt
    # "Never guess" alone made Claude return null instead of a draft for a JD without one.
    assert "The one exception is why_we_need_you" in prompt
    assert "you must write one" in prompt


def test_salary_comes_back_raw_so_code_decides_what_fits() -> None:
    salary = ParsedJd.model_fields["salary"]

    assert "currency" in prompt_fields(salary.annotation)
    assert "period" in prompt_fields(salary.annotation)


def prompt_fields(annotation: Any) -> set[str]:
    model = next(arg for arg in annotation.__args__ if arg is not type(None))
    return set(model.model_fields)


def test_why_we_need_you_is_asked_for_after_the_duties() -> None:
    # Asked first, Claude flagged a draft but left the text null: it had not read the duties yet.
    order = list(ParsedJd.model_fields)

    assert order.index("why_we_need_you") > order.index("roles")
    assert order.index("why_we_need_you") > order.index("qualifications")
    assert order[-2:] == ["why_we_need_you_drafted", "why_we_need_you"]


def test_no_requirement_bullet_is_dropped() -> None:
    # Sonnet dropped "5 năm kinh nghiệm bán hàng B2B, ưu tiên ngành thanh toán" from both lists.
    prompt = instructions()

    assert "never drop one" in prompt
    assert "stays whole in qualifications" in prompt


def test_the_prompt_files_the_working_arrangement_under_benefits() -> None:
    # Hours, days and contract terms sat outside any benefits block and were lost.
    prompt = instructions()

    assert "Also the working arrangement wherever the JD states it" in prompt


def test_the_prompt_keeps_one_language_of_a_bilingual_jd() -> None:
    # A bilingual form came back with every duty twice, once per language.
    assert "Never output an item and its translation as two items" in instructions()


def test_a_job_outside_vietnam_gets_the_abroad_code_and_keeps_its_country() -> None:
    prompt = instructions()

    assert "abroad = Outside Vietnam" in prompt
    assert "outside Vietnam" in prompt.split("- location")[1].split("- city")[0]
