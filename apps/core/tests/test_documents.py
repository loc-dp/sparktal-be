import base64
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from pydantic import BaseModel

from apps.core.documents import (
    MAX_DOCUMENT_BYTES,
    extract_text,
    is_scanned,
    read_with_claude,
    source_block,
    validate_document,
)
from apps.core.testing.pdf import make_pdf


class Shape(BaseModel):
    title: str | None


def ask(response: Any = None, error: Exception | None = None, **overrides: Any) -> Any:
    def parse(**_: Any) -> Any:
        if error is not None:
            raise error
        return response

    return read_with_claude(
        SimpleNamespace(messages=SimpleNamespace(parse=parse)),
        **{
            "model": "claude-sonnet-5-5",
            "source": {"type": "text", "text": "<jd>x</jd>"},
            "instructions": "Read it.",
            "output_format": Shape,
            "max_tokens": 100,
            "output_config": {"effort": "low"},
            "label": "JD",
            **overrides,
        },
    )


def reply(stop_reason: str = "end_turn", title: str = "Kế toán trưởng") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=Shape(title=title) if stop_reason == "end_turn" else None,
        model="claude-sonnet-5-5-20261001",
        usage=SimpleNamespace(input_tokens=900, output_tokens=120),
        _request_id="req_doc",
    )


def test_the_caller_names_the_document_in_type_and_size_errors() -> None:
    with pytest.raises(ValidationError, match="as a JD"):
        validate_document(SimpleUploadedFile("jd.png", b"\x89PNG"), "Upload it as a JD.", "big")
    with pytest.raises(ValidationError, match="JDs must be small"):
        validate_document(
            SimpleUploadedFile("jd.pdf", make_pdf("x") + b"0" * MAX_DOCUMENT_BYTES),
            "type",
            "JDs must be small",
        )


def test_a_pdf_text_layer_is_extracted_and_the_pointer_rewound() -> None:
    upload = SimpleUploadedFile("jd.pdf", make_pdf("Senior Accountant\nHo Chi Minh City"))

    assert "Senior Accountant" in extract_text(upload)
    assert upload.tell() == 0


def test_only_a_pdf_without_text_counts_as_scanned() -> None:
    assert is_scanned("jd.pdf", "")
    assert not is_scanned("jd.docx", "")
    assert not is_scanned("jd.pdf", "x" * 200)


def test_text_is_wrapped_in_the_callers_tag() -> None:
    block = source_block(b"PK", "jd.docx", "Kế toán trưởng", "jd")

    assert block == {"type": "text", "text": "<jd>\nKế toán trưởng\n</jd>"}


def test_a_pdf_goes_as_a_document_without_leading_junk() -> None:
    block = source_block(b"\r\n%PDF-1.4 x", "jd.pdf", "", "jd")

    assert block is not None
    assert block["type"] == "document"
    assert base64.standard_b64decode(block["source"]["data"]) == b"%PDF-1.4 x"  # type: ignore[typeddict-item]


def test_no_text_and_no_pdf_means_nothing_to_send() -> None:
    assert source_block(b"\xd0\xcf", "jd.doc", "", "jd") is None


def test_a_reply_returns_fields_model_and_tokens() -> None:
    read = ask(reply())

    assert read.fields == {"title": "Kế toán trưởng"}
    assert read.model == "claude-sonnet-5-5-20261001"
    assert (read.input_tokens, read.output_tokens) == (900, 120)


def test_an_unusable_reply_keeps_its_cost_and_logs_the_label(
    caplog: pytest.LogCaptureFixture,
) -> None:
    read = ask(reply(stop_reason="max_tokens"))

    assert read.fields is None
    assert read.output_tokens == 120
    assert "Claude JD parse stopped with max_tokens" in caplog.text


def test_an_api_error_returns_nothing() -> None:
    assert ask(error=anthropic.APIConnectionError(request=None)) is None


def test_a_broken_reply_is_logged_without_its_content(caplog: pytest.LogCaptureFixture) -> None:
    assert ask(error=ValueError("raw reply: Velapay secret salary 99")) is None
    assert "Claude JD parse returned unusable output (ValueError)" in caplog.text
    assert "Velapay" not in caplog.text
