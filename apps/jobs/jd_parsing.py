"""Read a job description with Claude. Output is raw; pass it through normalize_jd()."""

from __future__ import annotations

from typing import Literal

import anthropic
from anthropic.types import OutputConfigParam
from django.conf import settings
from pydantic import BaseModel

from apps.companies.models import Location
from apps.core.documents import ClaudeRead, anthropic_client, read_with_claude, source_block

# Long JDs list 30+ bullets copied word for word; 8000 leaves room for that.
MAX_OUTPUT_TOKENS = 8000
OUTPUT_CONFIG: OutputConfigParam = {"effort": "low"}


class Salary(BaseModel):
    minimum: int | None
    maximum: int | None
    currency: str | None
    period: Literal["hour", "day", "week", "month", "year"] | None


class ParsedJd(BaseModel):
    is_job_description: bool
    title: str | None
    roles: list[str]
    qualifications: list[str]
    nice_to_have: list[str]
    benefits: list[str]
    salary: Salary | None
    pay_basis: Literal["gross", "net"] | None
    work_model: Literal["onsite", "hybrid", "remote"] | None
    city: str | None
    skills: list[str]
    # Last, so a draft is written after the duties it is drawn from have been read out.
    why_we_need_you_drafted: bool
    why_we_need_you: str | None


def instructions() -> str:
    codes = ", ".join(f"{value} = {label}" for value, label in Location.choices if value != "other")
    return (
        "Extract this job description (JD) into a job posting form. The JD may be in "
        "Vietnamese, English or both.\n"
        "Rules:\n"
        "- Copy text verbatim, in the JD's own language and wording. Never translate, "
        "summarise, reword or fix grammar. Use null (or an empty list) for anything the JD "
        "does not state. Never guess. The one exception is why_we_need_you, below.\n"
        "- is_job_description: false if the file is not a job description (e.g. a CV, an "
        "invoice); then leave everything else empty.\n"
        "- title: the job title only, without the company name, location, salary or "
        "headcount ('Senior Backend Engineer', not 'Senior Backend Engineer - HCM - Up to "
        "$3000').\n"
        "- roles: every responsibility or duty, one list item per bullet or sentence as "
        "written in the JD, in the JD's order, without the bullet character.\n"
        "- qualifications: every must-have requirement (experience, education, skills, "
        "languages), one item per bullet, verbatim.\n"
        "- nice_to_have: only requirements the JD marks as optional or preferred ('a plus', "
        "'preferred', 'nice to have', 'ưu tiên', 'là lợi thế', 'là một điểm cộng'). Move "
        "them out of qualifications; never list an item twice.\n"
        "- Every requirement bullet in the JD ends up in exactly one of qualifications or "
        "nice_to_have; never drop one. A bullet that is a must-have with a preference "
        "inside it ('5 years in B2B sales, preferably in payments') stays whole in "
        "qualifications.\n"
        "- benefits: every perk, benefit or allowance, one item per bullet, verbatim. Leave "
        "out the salary range itself.\n"
        "- why_we_need_you: the JD's own paragraph on why the role exists or what it is for "
        "('About the role', 'Job purpose', 'Mục tiêu công việc', 'Tổng quan vị trí'), copied "
        "verbatim with why_we_need_you_drafted false. Not the company's 'About us'. If there "
        "is no such paragraph, you must write one: 1-3 sentences in the JD's language that "
        "say what this hire will own and why, using only facts and wording found in the JD, "
        "and set why_we_need_you_drafted true. Leave it null only when the JD lists no "
        "duties at all.\n"
        "- salary: the pay range as written, with amounts as whole numbers in full units "
        "('15 - 20 triệu' = 15000000 and 20000000; '$1,500' = 1500; '25M' = 25000000). "
        "'Up to X' fills only maximum; 'from X' only minimum; a single figure fills both. "
        "currency as an ISO code (VND, USD, ...). period is the unit the amount is paid per; "
        "null if the JD does not say. salary is null when pay is 'negotiable', "
        "'competitive', 'thỏa thuận' or not given.\n"
        "- pay_basis: 'gross' or 'net' only when the JD says so; else null.\n"
        "- work_model: only when the JD states it: 'remote' (fully remote / làm việc từ xa), "
        "'hybrid' (part office, part home), 'onsite' (explicitly full-time at the office). "
        "An office address alone is not a work model: null.\n"
        f"- city: where the job is based, as one of these codes: {codes}. Null if the JD "
        "names several cities, any other city, or none.\n"
        "- skills: up to 20 professional skills, tools or technologies the JD names, most "
        "important first, each a short name ('Go', 'IFRS', 'Figma'), not a sentence."
    )


def _client() -> anthropic.Anthropic:
    return anthropic_client(settings.ANTHROPIC_API_KEY, settings.JD_PARSE_TIMEOUT_SECONDS)


def is_enabled() -> bool:
    return bool(settings.ANTHROPIC_API_KEY and settings.JD_PARSE_MODEL)


def llm_extract(data: bytes, filename: str, text: str) -> ClaudeRead | None:
    """Return Claude's raw fields and cost, or None when no reply came back at all."""
    if not is_enabled():
        return None
    source = source_block(data, filename, text, "jd")
    if source is None:
        return None
    return read_with_claude(
        _client(),
        model=settings.JD_PARSE_MODEL,
        source=source,
        instructions=instructions(),
        output_format=ParsedJd,
        max_tokens=MAX_OUTPUT_TOKENS,
        output_config=OUTPUT_CONFIG,
        label="JD",
    )
