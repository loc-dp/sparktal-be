"""Read a CV with Claude. Output is raw; pass it through normalize_fields()."""

from __future__ import annotations

import anthropic
from anthropic.types import OutputConfigParam
from django.conf import settings
from django.utils import timezone
from pydantic import BaseModel

from apps.companies.models import Location
from apps.core.documents import ClaudeRead, anthropic_client, read_with_claude, source_block

__all__ = ["ClaudeRead", "ParsedCv", "instructions", "llm_extract"]

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
    return anthropic_client(settings.ANTHROPIC_API_KEY, settings.CV_PARSE_TIMEOUT_SECONDS)


def llm_extract(data: bytes, filename: str, text: str) -> ClaudeRead | None:
    """Return Claude's raw fields and cost, or None when no reply came back at all."""
    if not (settings.ANTHROPIC_API_KEY and settings.CV_PARSE_MODEL):
        return None
    source = source_block(data, filename, text, "cv")
    if source is None:
        return None
    return read_with_claude(
        _client(),
        model=settings.CV_PARSE_MODEL,
        source=source,
        instructions=instructions(),
        output_format=ParsedCv,
        max_tokens=MAX_OUTPUT_TOKENS,
        output_config=OUTPUT_CONFIG,
        label="CV",
    )
