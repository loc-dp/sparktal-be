"""Read a CV with Claude. Output is raw; pass it through normalize_fields()."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
from anthropic.types import ContentBlockParam, OutputConfigParam
from django.conf import settings
from django.utils import timezone
from pydantic import BaseModel

from apps.companies.models import Location

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 4000
OUTPUT_CONFIG: OutputConfigParam = {"effort": "low"}


class JobPeriod(BaseModel):
    start: str | None
    end: str | None


class ParsedCv(BaseModel):
    full_name: str | None
    current_title: str | None
    email: str | None
    phone: str | None
    dob: str | None
    location: str | None
    is_expat: bool | None
    languages: list[str]
    skills: list[str]
    jobs: list[JobPeriod]
    years_experience: int | None
    current_salary: int | None
    expected_salary: int | None


@dataclass(frozen=True)
class ClaudeRead:
    """What one call returned and cost. fields is None when the reply was unusable."""

    fields: dict[str, Any] | None
    model: str
    input_tokens: int
    output_tokens: int


def instructions() -> str:
    codes = ", ".join(f"{value} = {label}" for value, label in Location.choices)
    return (
        "Extract the candidate's details from this CV. It may be in Vietnamese, English or both.\n"
        f"Today is {timezone.localdate().isoformat()}.\n"
        "Rules:\n"
        "- Use null (or an empty list) for anything the CV does not state. Never guess.\n"
        "- full_name: the candidate's full name exactly as written, keeping every Vietnamese "
        "diacritic (Đào, not Dao). Leave out nicknames in brackets ('(Vicky)'), honorifics "
        "and degrees ('Ph.D.', 'MBA'). If the name is in Chinese, Korean or another "
        "non-Latin script, give its Vietnamese or English spelling, taken from elsewhere "
        "in the CV (email, publications) when it appears there; never an English alias "
        "on its own.\n"
        "- current_title: the job title of the current or most recent position.\n"
        "- email, phone: the candidate's own, only one each (the first if several), "
        "copied exactly as written in the CV.\n"
        "- dob: date of birth as YYYY-MM-DD; null if only an age or a year is given.\n"
        f"- location: where the candidate lives, as one of these codes: {codes}. "
        "Use 'other' for any other Vietnamese province; null if abroad or not stated.\n"
        "- is_expat: true only if the candidate is clearly a foreign national; else null.\n"
        "- languages: spoken languages in English, e.g. ['Vietnamese', 'English'].\n"
        "- skills: up to 20 professional skills, most important first, each a short name.\n"
        "- jobs: every paid job in the work history, not education, student research or "
        "internships. start and end as written: 'YYYY-MM' when the month is given, 'YYYY' "
        "when only the year is; end is 'present' for a current job, null if not stated. "
        "Copy the dates only; do not add them up.\n"
        "- years_experience: only a total the CV states outright ('8 years of experience'); "
        "else null.\n"
        "- current_salary, expected_salary: monthly gross in VND as a whole number "
        "('25 triệu' = 25000000); null if not stated or not in VND."
    )


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        timeout=settings.CV_PARSE_TIMEOUT_SECONDS,
        max_retries=1,
    )


PDF_HEADER = b"%PDF-"
# Readers accept junk before the header within the first 1024 bytes; Claude does not.
PDF_HEADER_WINDOW = 1024


def _source_block(data: bytes, filename: str, text: str) -> ContentBlockParam | None:
    if Path(filename).suffix.lower() == ".pdf":
        start = data.find(PDF_HEADER, 0, PDF_HEADER_WINDOW)
        if start > 0:
            data = data[start:]
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(data).decode("ascii"),
            },
        }
    if text:
        return {"type": "text", "text": f"<cv>\n{text}\n</cv>"}
    return None


def llm_extract(data: bytes, filename: str, text: str) -> ClaudeRead | None:
    """Return Claude's raw fields and cost, or None when no reply came back at all."""
    if not (settings.ANTHROPIC_API_KEY and settings.CV_PARSE_MODEL):
        return None
    source = _source_block(data, filename, text)
    if source is None:
        return None
    try:
        response = _client().messages.parse(
            model=settings.CV_PARSE_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            output_config=OUTPUT_CONFIG,
            messages=[
                {"role": "user", "content": [source, {"type": "text", "text": instructions()}]}
            ],
            output_format=ParsedCv,
        )
    except anthropic.APIError:
        logger.warning("Claude CV parse failed", exc_info=True)
        return None
    except ValueError as error:
        # A pydantic error quotes the raw reply, which holds candidate data: log its type only.
        logger.warning("Claude CV parse returned unusable output (%s)", type(error).__name__)
        return None
    fields = None
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        logger.warning(
            "Claude CV parse stopped with %s (request %s)",
            response.stop_reason,
            response._request_id,
        )
    else:
        fields = response.parsed_output.model_dump()
    return ClaudeRead(
        fields=fields,
        model=response.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
