import base64
from types import SimpleNamespace
from typing import Any

import anthropic
import pydantic
import pytest
from django.conf import settings as django_settings

from apps.candidates import llm_parsing
from apps.candidates.llm_parsing import ParsedCv, llm_extract

PDF = b"%PDF-1.4 fake"
TEXT = "Truong My Hanh\nmyhanh.truong@gmail.com"


class FakeMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def reply(stop_reason: str = "end_turn", **overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "is_cv": True,
        "full_name": "Trương Mỹ Hạnh",
        "current_title": "Payments Backend Lead",
        "email": "myhanh.truong@gmail.com",
        "phone": "0909 172 846",
        "dob": None,
        "location": "hcm",
        "is_expat": None,
        "languages": ["Vietnamese", "English"],
        "skills": ["Go", "Kafka"],
        "jobs": [],
        "years_experience": 9,
        "current_salary": None,
        "expected_salary": None,
        **overrides,
    }
    return SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=ParsedCv(**fields),
        model="claude-haiku-5-5-20261001",
        usage=SimpleNamespace(input_tokens=8400, output_tokens=520),
        _request_id="req_test",
    )


@pytest.fixture
def with_key(settings: Any) -> Any:
    settings.ANTHROPIC_API_KEY = "test-key"
    settings.CV_PARSE_MODEL = "claude-sonnet-5-5"
    return settings


def fake_client(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> FakeMessages:
    messages = FakeMessages(**kwargs)
    monkeypatch.setattr(llm_parsing, "_client", lambda: SimpleNamespace(messages=messages))
    return messages


def test_test_settings_never_carry_an_api_key() -> None:
    assert django_settings.ANTHROPIC_API_KEY == ""


def test_without_a_key_claude_is_never_called(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = fake_client(monkeypatch, response=reply())

    assert llm_extract(PDF, "cv.pdf", TEXT) is None
    assert messages.calls == []


def test_a_key_without_a_model_never_calls_claude(
    monkeypatch: pytest.MonkeyPatch, with_key: Any
) -> None:
    with_key.CV_PARSE_MODEL = ""
    messages = fake_client(monkeypatch, response=reply())

    assert llm_extract(PDF, "cv.pdf", TEXT) is None
    assert messages.calls == []


def test_a_pdf_is_sent_as_a_document_block(monkeypatch: pytest.MonkeyPatch, with_key: Any) -> None:
    messages = fake_client(monkeypatch, response=reply())

    read = llm_extract(PDF, "cv.pdf", TEXT)

    assert read is not None
    assert read.fields is not None
    assert read.fields["full_name"] == "Trương Mỹ Hạnh"
    assert read.fields["skills"] == ["Go", "Kafka"]
    call = messages.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["output_format"] is ParsedCv
    assert call["output_config"] == {"effort": "medium"}
    document = call["messages"][0]["content"][0]
    assert document["type"] == "document"
    assert base64.standard_b64decode(document["source"]["data"]) == PDF


def test_bytes_before_the_pdf_header_are_dropped(
    monkeypatch: pytest.MonkeyPatch, with_key: Any
) -> None:
    # pdf-lib exports can start with a newline; Claude rejects those as "not valid".
    messages = fake_client(monkeypatch, response=reply())

    llm_extract(b"\n" + PDF, "cv.pdf", TEXT)

    document = messages.calls[0]["messages"][0]["content"][0]
    assert base64.standard_b64decode(document["source"]["data"]) == PDF


def test_the_model_comes_from_settings(monkeypatch: pytest.MonkeyPatch, with_key: Any) -> None:
    with_key.CV_PARSE_MODEL = "claude-haiku-5-5"
    messages = fake_client(monkeypatch, response=reply())

    llm_extract(PDF, "cv.pdf", TEXT)

    assert messages.calls[0]["model"] == "claude-haiku-5-5"


def test_a_docx_is_sent_as_its_extracted_text(
    monkeypatch: pytest.MonkeyPatch, with_key: Any
) -> None:
    messages = fake_client(monkeypatch, response=reply())

    llm_extract(b"PK docx bytes", "cv.docx", TEXT)

    first = messages.calls[0]["messages"][0]["content"][0]
    assert first["type"] == "text"
    assert TEXT in first["text"]


def test_a_file_without_text_or_pdf_falls_back(
    monkeypatch: pytest.MonkeyPatch, with_key: Any
) -> None:
    messages = fake_client(monkeypatch, response=reply())

    assert llm_extract(b"\xd0\xcf\x11\xe0", "cv.doc", "") is None
    assert messages.calls == []


def test_an_api_failure_falls_back(monkeypatch: pytest.MonkeyPatch, with_key: Any) -> None:
    fake_client(monkeypatch, error=anthropic.APIConnectionError(request=None))

    assert llm_extract(PDF, "cv.pdf", TEXT) is None


def test_the_model_and_tokens_of_a_reply_are_reported(
    monkeypatch: pytest.MonkeyPatch, with_key: Any
) -> None:
    fake_client(monkeypatch, response=reply())

    read = llm_extract(PDF, "cv.pdf", TEXT)

    assert read is not None
    assert read.model == "claude-haiku-5-5-20261001"
    assert (read.input_tokens, read.output_tokens) == (8400, 520)


def test_a_refusal_falls_back_but_still_reports_its_tokens(
    monkeypatch: pytest.MonkeyPatch, with_key: Any
) -> None:
    # The tokens were billed even though the reply is unusable.
    fake_client(monkeypatch, response=reply(stop_reason="refusal"))

    read = llm_extract(PDF, "cv.pdf", TEXT)

    assert read is not None
    assert read.fields is None
    assert (read.input_tokens, read.output_tokens) == (8400, 520)


def truncated_output_error() -> pydantic.ValidationError:
    try:
        ParsedCv.model_validate_json('{"full_name": "Le Minh Tu", "email": "tu.le@gmail.com", "ye')
    except pydantic.ValidationError as error:
        return error
    raise AssertionError("expected a validation error")


def test_a_truncated_reply_falls_back_without_logging_cv_content(
    monkeypatch: pytest.MonkeyPatch, with_key: Any, caplog: pytest.LogCaptureFixture
) -> None:
    # The SDK raises while parsing a cut-off reply; its message quotes the raw model output.
    fake_client(monkeypatch, error=truncated_output_error())

    assert llm_extract(PDF, "cv.pdf", TEXT) is None
    assert "Claude CV parse" in caplog.text
    assert "tu.le@gmail.com" not in caplog.text
    assert "Le Minh Tu" not in caplog.text


def test_the_prompt_lists_every_location_code() -> None:
    prompt = llm_parsing.instructions()

    for code in ("hcm", "hn", "dn", "other"):
        assert code in prompt


def test_the_prompt_asks_for_one_contact_each() -> None:
    prompt = llm_parsing.instructions()

    assert "only one" in prompt


def test_the_prompt_keeps_diacritics_when_a_name_has_several_scripts() -> None:
    # "use the Latin-alphabet one" made Haiku drop diacritics: "Đào Phi Lâm" became "Dao Phi Lam".
    prompt = llm_parsing.instructions()

    assert "Latin-alphabet" not in prompt
    assert "Đào, not Dao" in prompt


def test_the_prompt_keeps_nicknames_and_degrees_out_of_the_name() -> None:
    # Sonnet returned "Đỗ Thị Quỳnh Châu (Vicky)", "Pham Ngoc Dinh, Ph.D." and, for a name
    # written in Chinese characters, only the English alias "Michael".
    prompt = llm_parsing.instructions()

    assert "nickname" in prompt
    assert "Ph.D." in prompt
    assert "alias" in prompt


def test_claude_copies_job_dates_instead_of_adding_up_years() -> None:
    # Summing overlapping ranges went wrong by 1-3 years on a third of real CVs; code does it now.
    prompt = llm_parsing.instructions()

    assert "jobs" in ParsedCv.model_fields
    assert "'present'" in prompt
    assert "counting overlapping" not in prompt


def test_claude_can_say_a_file_is_not_a_cv() -> None:
    # A child's speech-therapy report was read as a candidate born in 2021.
    assert "is_cv" in ParsedCv.model_fields
    assert "is_cv" in llm_parsing.instructions()


def test_the_title_comes_from_the_work_history_not_the_headline() -> None:
    # Sonnet 5.5 took "Head of Merchandise" from a TopCV banner (the role applied for)
    # over the latest job, "Merchandise", and gave an intern's thesis as the title.
    prompt = llm_parsing.instructions()

    assert "headline" in prompt
    assert "internship" in prompt.split("current_title")[1].split("- email")[0]


def test_the_prompt_never_adds_diacritics_the_cv_does_not_show() -> None:
    # "Hung, Vu Tien" became "Vũ Tiến Hùng": Hùng or Hưng is a guess.
    assert "never add diacritics" in llm_parsing.instructions()


def test_a_non_latin_name_without_a_printed_spelling_is_transliterated() -> None:
    # "never an English alias" plus "never guess" left 顏敏慎 (Jordan Yen) with no name at all.
    assert "pinyin" in llm_parsing.instructions()


def test_a_foreign_phone_gets_its_country_code() -> None:
    # The gate reads a bare "0922 550 993" as Vietnamese; for a Taichung address it is +886.
    assert "+886" in llm_parsing.instructions()


def test_location_is_not_guessed_from_a_school_or_employer() -> None:
    assert "school" in llm_parsing.instructions().split("- location")[1].split("- is_expat")[0]


def test_jobs_sharing_one_date_range_each_get_it() -> None:
    # "EARLIER CAREER, 2016-2019" listed four jobs; all four were dropped.
    assert "shared" in llm_parsing.instructions()


def test_the_real_name_beats_an_english_name_printed_beside_it() -> None:
    # "SALLY NGUYEN (NGUYEN THI XANH)": the bracket is the legal name, not a nickname.
    assert "NGUYEN THI XANH" in llm_parsing.instructions()


def test_a_comma_ordered_name_is_put_back_in_order() -> None:
    # A LinkedIn export prints "Hung, Vu Tien".
    assert "'Hung, Vu Tien'" in llm_parsing.instructions()


def test_a_title_in_capitals_comes_back_in_normal_case() -> None:
    # Copying "as written" brought back "SENIOR MERCHANDISER" and "QA FABRIC & TRIMS".
    rule = llm_parsing.instructions().split("- current_title")[1].split("- email")[0]

    assert "capitals" in rule
    assert "part-time" in rule


def test_an_internship_title_is_used_when_it_is_the_only_work() -> None:
    # jobs leaves internships out, so a graduate whose only work was one got a null title.
    rule = llm_parsing.instructions().split("- current_title")[1].split("- email")[0]

    assert "no paid job, give the latest internship's title" in rule


def test_the_headline_never_stands_in_for_an_internship_title() -> None:
    # A sales intern whose CV was headed "NHÂN VIÊN KHO" (the job applied for) got that
    # headline once the title rule told Claude to skip internships.
    assert "even when the only work is an internship" in title_rule()


def test_a_language_certificate_names_its_language() -> None:
    # "Do not add one the CV does not name" dropped English from "TOEIC 810".
    assert "TOEIC" in llm_parsing.instructions()


def test_a_candidate_living_abroad_gets_the_abroad_code() -> None:
    # Before Sparktal took jobs abroad, a Taipei address gave null, as if never stated.
    rule = llm_parsing.instructions().split("- location")[1].split("- is_expat")[0]

    assert "'abroad'" in rule
    assert "null if abroad" not in rule


def test_a_foreigner_living_in_vietnam_gets_their_city_not_abroad() -> None:
    # A Taiwanese CV whose address read 越南西貢 (Saigon) came back 'abroad'.
    rule = llm_parsing.instructions().split("- location")[1].split("- is_expat")[0]

    assert "whatever their nationality" in rule
    assert "西貢" in rule


def test_vietnam_alone_does_not_name_a_province() -> None:
    # A CV whose contact block said only "Vietnam" came back 'other'.
    rule = llm_parsing.instructions().split("- location")[1].split("- is_expat")[0]

    assert "only 'Vietnam'" in rule


def title_rule() -> str:
    return llm_parsing.instructions().split("- current_title")[1].split("- email")[0]


def test_a_paid_job_beats_a_newer_or_current_internship() -> None:
    # A cashier until 02/2026 then a tour-operations intern from 03/2026 got the intern title,
    # and a 2024 master's thesis beat the test engineer job before it.
    rule = title_rule()

    assert "newer or current" in rule
    assert "thesis" in rule


def test_the_latest_job_is_found_by_its_dates_not_its_place_on_the_page() -> None:
    # A two-column CV listed "Customer Service Staff (August 2024)" above "Sales Associate
    # (June 2025)", and the first one listed came back.
    assert "not by the order on the page" in title_rule()


def test_a_title_keeps_the_language_the_cv_writes_it_in() -> None:
    # "NHÂN VIÊN BÁN HÀNG" came back "Sales Staff" in some runs and not in others; a title
    # written in both languages flipped between them.
    rule = title_rule()

    assert "never translating it" in rule
    assert "give the Vietnamese" in rule


def test_a_vietnamese_title_comes_back_in_sentence_case() -> None:
    # "Nhân Viên Pha Chế" and "Nhân viên pha chế" alternated between runs.
    assert "sentence case" in title_rule()


def test_claude_lists_each_job_with_its_title_before_picking_the_current_one() -> None:
    # Without thinking, Claude picks in the order it writes: with the title first it took
    # "Customer Service Staff (August 2024)" over "Sales Associate (June 2025)".
    fields = list(ParsedCv.model_fields)

    assert "title" in llm_parsing.JobPeriod.model_fields
    assert fields.index("jobs") < fields.index("current_title")
    assert "the latest entry in jobs" in title_rule()
