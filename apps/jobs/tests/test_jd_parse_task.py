from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.companies.models import Company
from apps.companies.tests.factories import OfficeFactory
from apps.core.testing.pdf import make_pdf
from apps.jobs import jd_parsing
from apps.jobs.jd_parsing import ParsedJd
from apps.jobs.models import JdParse, JdParseStatus
from apps.jobs.tasks import (
    JD_PARSE_TTL,
    NOT_A_JD,
    OLD_DOC,
    UNREADABLE,
    parse_jd,
    purge_stale_jd_parses,
)
from apps.jobs.tests.factories import JdParseFactory

JD = "Kế toán trưởng\nTrách nhiệm\n- Lập báo cáo tài chính"
DOC_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def claude_returns(
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
    stop_reason: str = "end_turn",
    **fields: Any,
) -> list[dict[str, Any]]:
    settings.ANTHROPIC_API_KEY = "test-key"
    settings.JD_PARSE_MODEL = "claude-sonnet-5-5"
    calls: list[dict[str, Any]] = []
    base: dict[str, Any] = {
        "is_job_description": True,
        "title": "Kế toán trưởng",
        "why_we_need_you": None,
        "why_we_need_you_drafted": False,
        "roles": ["Lập báo cáo tài chính"],
        "qualifications": ["5 năm kinh nghiệm kế toán"],
        "nice_to_have": [],
        "benefits": [],
        "salary": {"minimum": 30_000_000, "maximum": 45_000_000, "currency": "VND", "period": None},
        "pay_basis": "gross",
        "work_model": None,
        "location": None,
        "city": "hcm",
        "skills": ["IFRS"],
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
    }

    def parse(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        ok = stop_reason == "end_turn"
        return SimpleNamespace(
            stop_reason=stop_reason,
            parsed_output=ParsedJd(**{**base, **fields}) if ok else None,
            model="claude-sonnet-5-5-20261001",
            usage=SimpleNamespace(input_tokens=2400, output_tokens=700),
            _request_id="req_t",
        )

    monkeypatch.setattr(
        jd_parsing, "_client", lambda: SimpleNamespace(messages=SimpleNamespace(parse=parse))
    )
    return calls


@pytest.fixture
def attempt(company: Company) -> JdParse:
    return cast(
        JdParse,
        JdParseFactory(company=company, file=ContentFile(make_pdf(JD), name="jd.pdf")),
    )


def test_claude_fields_are_validated_and_stored_with_their_cost(
    attempt: JdParse, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(monkeypatch, settings, bounty_pct=30)

    parse_jd(str(attempt.pk))

    attempt.refresh_from_db()
    assert attempt.status == JdParseStatus.DONE
    assert attempt.fields["title"] == "Kế toán trưởng"
    assert attempt.fields["roles"] == "Lập báo cáo tài chính"
    assert (attempt.fields["salary_min"], attempt.fields["salary_max"]) == (30_000_000, 45_000_000)
    assert "bounty_pct" not in attempt.fields
    assert attempt.llm_model == "claude-sonnet-5-5-20261001"
    assert (attempt.input_tokens, attempt.output_tokens) == (2400, 700)
    assert attempt.started_at is not None
    assert attempt.finished_at is not None


def test_the_office_is_matched_among_the_uploaders_company_offices(
    attempt: JdParse, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(monkeypatch, settings, city="hcm")
    head_office = attempt.company.offices.get()
    OfficeFactory(city="hcm")  # Another company's office in the same city is never a match.

    parse_jd(str(attempt.pk))

    attempt.refresh_from_db()
    assert attempt.fields["office"] == str(head_office.pk)


def test_two_offices_in_the_city_leave_the_office_blank(
    attempt: JdParse, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(monkeypatch, settings, city="hcm")
    OfficeFactory(company=attempt.company, city="hcm", position=1)

    parse_jd(str(attempt.pk))

    attempt.refresh_from_db()
    assert attempt.fields["office"] == ""


def test_an_unusable_reply_fails_but_keeps_its_cost(
    attempt: JdParse, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(monkeypatch, settings, stop_reason="max_tokens")

    parse_jd(str(attempt.pk))

    attempt.refresh_from_db()
    assert attempt.status == JdParseStatus.FAILED
    assert attempt.error == UNREADABLE
    assert (attempt.input_tokens, attempt.output_tokens) == (2400, 700)


def test_a_file_that_is_not_a_jd_fails_with_its_own_message(
    attempt: JdParse, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(monkeypatch, settings, is_job_description=False)

    parse_jd(str(attempt.pk))

    attempt.refresh_from_db()
    assert attempt.status == JdParseStatus.FAILED
    assert attempt.error == NOT_A_JD
    assert attempt.fields == {}


def test_an_old_doc_fails_with_a_pointer_to_pdf_without_calling_claude(
    company: Company, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    calls = claude_returns(monkeypatch, settings)
    attempt = JdParseFactory(
        company=company, filename="JD.doc", file=ContentFile(DOC_MAGIC + b"x", name="jd.doc")
    )

    parse_jd(str(attempt.pk))

    attempt.refresh_from_db()
    assert attempt.status == JdParseStatus.FAILED
    assert attempt.error == OLD_DOC
    assert calls == []


def test_an_unexpected_error_fails_the_parse(
    attempt: JdParse, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    claude_returns(monkeypatch, settings)

    def boom(*_: Any) -> None:
        raise RuntimeError("disk gone")

    monkeypatch.setattr("apps.jobs.tasks.normalize_jd", boom)

    parse_jd(str(attempt.pk))

    attempt.refresh_from_db()
    assert attempt.status == JdParseStatus.FAILED
    assert attempt.error == UNREADABLE


def test_a_deleted_parse_is_skipped(db: None) -> None:
    parse_jd("00000000-0000-0000-0000-000000000000")


def test_stale_parses_and_their_files_are_purged(company: Company) -> None:
    stale = cast(JdParse, JdParseFactory(company=company))
    fresh = cast(JdParse, JdParseFactory(company=company))
    JdParse.objects.filter(pk=stale.pk).update(
        created_at=timezone.now() - JD_PARSE_TTL - timedelta(minutes=1)
    )
    storage, name = stale.file.storage, stale.file.name

    assert purge_stale_jd_parses() == 1
    assert list(JdParse.objects.values_list("pk", flat=True)) == [fresh.pk]
    assert not storage.exists(name)


def test_the_purge_runs_on_the_beat_schedule(settings: Any) -> None:
    tasks = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}

    assert "jobs.purge_stale_jd_parses" in tasks
