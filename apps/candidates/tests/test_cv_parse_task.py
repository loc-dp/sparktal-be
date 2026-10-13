import io
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.files.base import ContentFile
from PIL import Image

from apps.candidates import llm_parsing, tasks
from apps.candidates.llm_parsing import ParsedCv
from apps.candidates.models import CvParse, CvParseReader, CvParseStatus
from apps.candidates.tasks import parse_cv, read_fields
from apps.candidates.tests.factories import CvParseFactory
from apps.core.testing.pdf import make_pdf
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db

CV = """Truong My Hanh
Payments Backend Lead

Email: myhanh.truong@gmail.com
Phone: 0909 172 846

Summary
9 years of experience building payment systems.

Skills
Go, Kafka, PostgreSQL
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
    "source",
}


def test_a_readable_pdf_fills_the_full_contract(recruiter_profile: RecruiterProfile) -> None:
    parse = CvParseFactory(owner=recruiter_profile, file=ContentFile(make_pdf(CV), name="cv.pdf"))

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.status == CvParseStatus.DONE
    assert parse.error == ""
    assert parse.finished_at is not None
    assert set(parse.fields) == CONTRACT
    assert parse.fields["full_name"] == "Truong My Hanh"
    assert parse.fields["email"] == "myhanh.truong@gmail.com"
    assert parse.fields["phone"] == "+84909172846"
    assert parse.fields["skills"] == ["Go", "Kafka", "PostgreSQL"]
    assert parse.fields["years_experience"] == 9
    assert parse.fields["source"] == "text"


def test_a_file_nothing_can_read_fails_with_the_unreadable_message(
    recruiter_profile: RecruiterProfile,
) -> None:
    # No text layer and no Claude: an empty "done" would read as "nothing to fill".
    parse = CvParseFactory(
        owner=recruiter_profile,
        filename="cv.pdf",
        file=ContentFile(b"%PDF-1.4 truncated", name="cv.pdf"),
    )

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.status == CvParseStatus.FAILED
    assert parse.error == "We couldn't read that CV. Fill the form in yourself."


def test_an_old_doc_file_asks_for_a_pdf(recruiter_profile: RecruiterProfile) -> None:
    # Neither the text reader nor Claude reads Word 97; "re-save it" is something the
    # recruiter can act on, unlike "couldn't read".
    parse = CvParseFactory(
        owner=recruiter_profile,
        filename="cv.doc",
        file=ContentFile(b"\xd0\xcf\x11\xe0 word 97", name="cv.doc"),
    )

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.status == CvParseStatus.FAILED
    assert parse.error == "We can't read old .doc files. Save the CV as PDF and upload it again."


def test_a_missing_blob_fails_with_a_readable_message(
    recruiter_profile: RecruiterProfile,
) -> None:
    parse = CvParseFactory(owner=recruiter_profile, file=ContentFile(make_pdf(CV), name="cv.pdf"))
    parse.file.delete(save=True)

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.status == CvParseStatus.FAILED
    assert parse.error == "We couldn't read that CV. Fill the form in yourself."
    assert parse.finished_at is not None


def test_an_unknown_id_is_ignored(recruiter_profile: RecruiterProfile) -> None:
    parse_cv(str(uuid.uuid4()))  # no exception: a retry must not crash the worker

    assert CvParse.objects.count() == 0


def image_pdf() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 560), "white").save(buffer, "PDF")
    return buffer.getvalue()


def claude_returns(
    monkeypatch: pytest.MonkeyPatch, settings: Any, **fields: Any
) -> list[dict[str, Any]]:
    settings.ANTHROPIC_API_KEY = "test-key"
    settings.CV_PARSE_MODEL = "claude-sonnet-5-5"
    base: dict[str, Any] = {
        "is_cv": True,
        "full_name": None,
        "current_title": None,
        "email": None,
        "phone": None,
        "dob": None,
        "location": None,
        "is_expat": None,
        "languages": [],
        "skills": [],
        "jobs": [],
        "years_experience": None,
        "current_salary": None,
        "expected_salary": None,
    }
    calls: list[dict[str, Any]] = []

    def parse(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            parsed_output=ParsedCv(**{**base, **fields}),
            model="claude-sonnet-5-5-20261001",
            usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
            _request_id="req_t",
        )

    monkeypatch.setattr(
        llm_parsing, "_client", lambda: SimpleNamespace(messages=SimpleNamespace(parse=parse))
    )
    return calls


def test_claude_fields_are_used_and_validated(
    recruiter_profile: RecruiterProfile, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(
        monkeypatch,
        settings,
        full_name="TRƯƠNG MỸ HẠNH",
        current_title="Payments Backend Lead",
        email="invented@example.com",
        location="hcm",
        skills=["Go", "Kafka"],
        years_experience=9,
    )
    parse = CvParseFactory(owner=recruiter_profile, file=ContentFile(make_pdf(CV), name="cv.pdf"))

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.fields["full_name"] == "Trương Mỹ Hạnh"
    assert parse.fields["location"] == "hcm"
    # Claude's email is not in the PDF text; the regex find wins instead.
    assert parse.fields["email"] == "myhanh.truong@gmail.com"
    assert parse.fields["source"] == "text"


def test_a_scanned_pdf_goes_to_claude_and_is_flagged(
    recruiter_profile: RecruiterProfile, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    calls = claude_returns(
        monkeypatch,
        settings,
        full_name="Lê Minh Tú",
        email="tu.le@gmail.com",
        phone="0912 345 678",
    )
    parse = CvParseFactory(
        owner=recruiter_profile, filename="scan.pdf", file=ContentFile(image_pdf(), name="scan.pdf")
    )

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.status == CvParseStatus.DONE
    assert calls[0]["messages"][0]["content"][0]["type"] == "document"
    assert parse.fields["source"] == "scanned"
    assert parse.fields["full_name"] == "Lê Minh Tú"
    # No text layer to check against, so a well-formed contact is kept.
    assert parse.fields["email"] == "tu.le@gmail.com"
    assert parse.fields["phone"] == "+84912345678"


def test_claude_down_still_finishes_with_regex_fields(
    recruiter_profile: RecruiterProfile, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.ANTHROPIC_API_KEY = "test-key"
    settings.CV_PARSE_MODEL = "claude-sonnet-5-5"

    def boom(**_: Any) -> None:
        raise llm_parsing.anthropic.APIConnectionError(request=None)

    monkeypatch.setattr(
        llm_parsing, "_client", lambda: SimpleNamespace(messages=SimpleNamespace(parse=boom))
    )
    parse = CvParseFactory(owner=recruiter_profile, file=ContentFile(make_pdf(CV), name="cv.pdf"))

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.status == CvParseStatus.DONE
    assert parse.fields["full_name"] == "Truong My Hanh"
    assert parse.fields["skills"] == ["Go", "Kafka", "PostgreSQL"]


def test_a_scan_with_a_stray_text_stamp_keeps_claudes_contacts(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(monkeypatch, settings, email="tu.le@gmail.com", phone="0912 345 678")

    fields, _ = read_fields(b"%PDF-1.4 scan", "scan.pdf", "Scanned with CamScanner")

    assert fields["source"] == "scanned"
    assert fields["email"] == "tu.le@gmail.com"
    assert fields["phone"] == "+84912345678"


def test_a_scan_ignores_contacts_in_its_stray_text_layer(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(monkeypatch, settings, full_name="Lê Minh Tú")

    fields, _ = read_fields(b"%PDF-1.4 scan", "scan.pdf", "CamScanner support@camscanner.com")

    assert fields["full_name"] == "Lê Minh Tú"
    assert fields["email"] == ""


def test_claudes_email_beats_a_regex_find_cut_by_a_line_break(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(monkeypatch, settings, email="tranvanchung290597@gmail.com")
    text = CV + "\nContact\ntranvanchung290597@gmail.co\nm\n"

    fields, _ = read_fields(b"%PDF-1.4", "cv.pdf", text)

    assert fields["email"] == "tranvanchung290597@gmail.com"


def test_claudes_foreign_phone_is_not_replaced_by_regex_noise(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(monkeypatch, settings, phone="+886917962702")
    monkeypatch.setattr(tasks, "guess_fields", lambda _: {"phone": "+8408201020042"})
    text = CV + "\nContact\n+886917962702\nPhone\n08/2010 - 2004\n"

    fields, _ = read_fields(b"%PDF-1.4", "cv.pdf", text)

    assert fields["phone"] == "+886917962702"


def test_a_referees_contacts_never_fill_the_candidates(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    # The CV gives no email or phone of the candidate's own, only the referee's.
    claude_returns(monkeypatch, settings, full_name="Vũ Văn Nam")
    text = "Vu Van Nam\nProduction Manager\n\nReferences\nDr. Hisato Takeda\n"
    text += "takeda.vn@goshukohsan.com\n0904 658 169\n"

    fields, _ = read_fields(b"%PDF-1.4", "cv.pdf", text)

    assert fields["full_name"] == "Vũ Văn Nam"
    assert fields["email"] == ""
    assert fields["phone"] == ""


def test_a_file_that_is_not_a_cv_fails_and_keeps_its_cost(
    recruiter_profile: RecruiterProfile, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    # A therapy report for a child still has a name, a date and an address to extract.
    claude_returns(monkeypatch, settings, is_cv=False, full_name="Mai Thế An")
    parse = CvParseFactory(owner=recruiter_profile, file=ContentFile(make_pdf(CV), name="cv.pdf"))

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.status == CvParseStatus.FAILED
    assert parse.error == "That file doesn't look like a CV. Fill the form in yourself."
    assert parse.fields == {}
    assert (parse.input_tokens, parse.output_tokens) == (1200, 300)


def test_a_claude_read_records_the_reader_model_and_tokens(
    recruiter_profile: RecruiterProfile, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(monkeypatch, settings, full_name="Trương Mỹ Hạnh")
    parse = CvParseFactory(owner=recruiter_profile, file=ContentFile(make_pdf(CV), name="cv.pdf"))

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.reader == CvParseReader.CLAUDE
    assert parse.llm_model == "claude-sonnet-5-5-20261001"
    assert (parse.input_tokens, parse.output_tokens) == (1200, 300)
    assert parse.started_at is not None
    assert parse.finished_at is not None
    assert parse.started_at <= parse.finished_at


def test_a_rules_read_records_no_model_or_tokens(recruiter_profile: RecruiterProfile) -> None:
    parse = CvParseFactory(owner=recruiter_profile, file=ContentFile(make_pdf(CV), name="cv.pdf"))

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.reader == CvParseReader.RULES
    assert parse.llm_model == ""
    assert (parse.input_tokens, parse.output_tokens) == (0, 0)


def test_an_unusable_claude_reply_falls_back_to_rules_but_keeps_its_cost(
    recruiter_profile: RecruiterProfile, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(monkeypatch, settings)
    monkeypatch.setattr(
        llm_parsing,
        "_client",
        lambda: SimpleNamespace(
            messages=SimpleNamespace(
                parse=lambda **_: SimpleNamespace(
                    stop_reason="max_tokens",
                    parsed_output=None,
                    model="claude-sonnet-5-5-20261001",
                    usage=SimpleNamespace(input_tokens=1200, output_tokens=4000),
                    _request_id="req_t",
                )
            )
        ),
    )
    parse = CvParseFactory(owner=recruiter_profile, file=ContentFile(make_pdf(CV), name="cv.pdf"))

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.status == CvParseStatus.DONE
    assert parse.reader == CvParseReader.RULES
    assert parse.llm_model == "claude-sonnet-5-5-20261001"
    assert (parse.input_tokens, parse.output_tokens) == (1200, 4000)


def test_an_unreadable_scan_keeps_the_cost_of_asking_claude(
    recruiter_profile: RecruiterProfile, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(monkeypatch, settings)
    monkeypatch.setattr(
        llm_parsing,
        "_client",
        lambda: SimpleNamespace(
            messages=SimpleNamespace(
                parse=lambda **_: SimpleNamespace(
                    stop_reason="refusal",
                    parsed_output=None,
                    model="claude-sonnet-5-5-20261001",
                    usage=SimpleNamespace(input_tokens=1500, output_tokens=10),
                    _request_id="req_t",
                )
            )
        ),
    )
    parse = CvParseFactory(
        owner=recruiter_profile, filename="scan.pdf", file=ContentFile(image_pdf(), name="scan.pdf")
    )

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.status == CvParseStatus.FAILED
    assert parse.reader == ""
    assert (parse.input_tokens, parse.output_tokens) == (1500, 10)


def test_years_come_from_claudes_job_dates_not_its_arithmetic(
    monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(
        monkeypatch,
        settings,
        jobs=[
            {"title": "Backend Engineer", "start": "2015-01", "end": "2020-01"},
            {"title": "Mentor", "start": "2018-01", "end": "2020-01"},
        ],
        years_experience=7,
    )

    fields, _ = read_fields(b"%PDF-1.4", "cv.pdf", CV)

    assert fields["years_experience"] == 5
