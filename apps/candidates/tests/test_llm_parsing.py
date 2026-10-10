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
    assert call["output_config"] == {"effort": "low"}
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
