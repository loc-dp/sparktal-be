import csv
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.candidates import llm_parsing
from apps.candidates.llm_parsing import ParsedCv
from apps.core.testing.pdf import make_pdf

CV = "Truong My Hanh\nPayments Backend Lead\nEmail: myhanh.truong@gmail.com\n"


def test_it_writes_one_row_per_file_and_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.ANTHROPIC_API_KEY = "test-key"
    settings.CV_PARSE_MODEL = "claude-sonnet-5-5"
    output = ParsedCv(
        is_cv=True,
        full_name="Trương Mỹ Hạnh",
        current_title=None,
        email=None,
        phone=None,
        dob=None,
        location=None,
        is_expat=None,
        languages=[],
        skills=["Go"],
        jobs=[],
        years_experience=None,
        current_salary=None,
        expected_salary=None,
    )
    reply = SimpleNamespace(
        stop_reason="end_turn",
        parsed_output=output,
        model="claude-sonnet-5-5",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        _request_id="r",
    )
    monkeypatch.setattr(
        llm_parsing,
        "_client",
        lambda: SimpleNamespace(messages=SimpleNamespace(parse=lambda **_: reply)),
    )
    (tmp_path / "hanh.pdf").write_bytes(make_pdf(CV))
    (tmp_path / "notes.txt").write_text("ignored")
    out = tmp_path / "report.csv"

    call_command("compare_cv_parsers", str(tmp_path), out=str(out))

    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert {row["file"] for row in rows} == {"hanh.pdf"}
    name = next(row for row in rows if row["field"] == "full_name")
    assert name["regex"] == "Truong My Hanh"
    assert name["claude"] == "Trương Mỹ Hạnh"


def test_it_refuses_to_run_without_claude_settings(tmp_path: Path) -> None:
    with pytest.raises(CommandError, match="ANTHROPIC_API_KEY"):
        call_command("compare_cv_parsers", str(tmp_path))
