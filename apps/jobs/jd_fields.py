"""Clean JD fields from Claude so every value fits the Create new job form."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from apps.companies.models import Location
from apps.jobs.models import PayBasis, WorkModel

MAX_TITLE_LENGTH = 120
MAX_WHY_LENGTH = 1500
MAX_ITEMS = 40
MAX_ITEM_LENGTH = 600
MAX_SKILLS = 20
MAX_SKILL_LENGTH = 40
# A monthly salary below 1 million VND is a figure in millions Claude did not expand ("15").
MIN_MONTHLY_VND = 1_000_000
MAX_MONTHLY_VND = 10_000_000_000
VND_SPELLINGS = {"vnd", "vnđ", "₫", "đ", "dong", "đồng"}
LIST_FIELDS = ("roles", "qualifications", "nice_to_have", "benefits")
# Bullets, check marks and numbering ("1.", "2)", "a.") that a JD puts before an item.
BULLET = re.compile(r"^(?:[•●○◦▪▫■□►▸✓✔➢➤\-–—*+·]|(?:\d{1,2}|[a-zA-Z])[.)](?=\s))\s*")


@dataclass(frozen=True)
class OfficeRef:
    id: str
    city: str


def _squeeze(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _title(value: Any) -> str:
    title = _squeeze(value)
    if len(title) <= MAX_TITLE_LENGTH:
        return title
    return title[: MAX_TITLE_LENGTH + 1].rsplit(" ", 1)[0]


def _paragraphs(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    lines = (_squeeze(line) for line in value.splitlines())
    return "\n".join(line for line in lines if line)[:MAX_WHY_LENGTH].strip()


def _items(value: Any) -> list[str]:
    """One clean item per line, bullets stripped, repeats dropped (ignoring case)."""
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, str):
            continue
        for line in entry.splitlines():
            item = BULLET.sub("", _squeeze(line), count=1).strip()[:MAX_ITEM_LENGTH]
            if item and item.lower() not in seen:
                items.append(item)
                seen.add(item.lower())
    return items[:MAX_ITEMS]


def _lists(raw: dict[str, Any]) -> dict[str, str]:
    lists = {field: _items(raw.get(field)) for field in LIST_FIELDS}
    optional = {item.lower() for item in lists["nice_to_have"]}
    lists["qualifications"] = [
        item for item in lists["qualifications"] if item.lower() not in optional
    ]
    return {field: "\n".join(items) for field, items in lists.items()}


def _amount(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if MIN_MONTHLY_VND <= value <= MAX_MONTHLY_VND else None


def _salary(value: Any) -> tuple[int | None, int | None]:
    """Only a monthly VND range fills the form; USD, yearly or odd figures stay blank."""
    if not isinstance(value, dict):
        return None, None
    currency = _squeeze(value.get("currency")).lower()
    if currency not in VND_SPELLINGS or value.get("period") not in ("month", None):
        return None, None
    low, high = _amount(value.get("minimum")), _amount(value.get("maximum"))
    if value.get("minimum") is not None and low is None:
        return None, None
    if value.get("maximum") is not None and high is None:
        return None, None
    if low is not None and high is not None and high < low:
        return None, None
    return low, high


def _choice(value: Any, choices: list[str]) -> str:
    return value if isinstance(value, str) and value in choices else ""


def _office(city: Any, work_model: str, offices: list[OfficeRef]) -> str:
    """The company's only office in that city; no guess when there are none or several."""
    if work_model == WorkModel.REMOTE or city == Location.OTHER:
        return ""
    matches = [office for office in offices if office.city == city]
    return matches[0].id if len(matches) == 1 else ""


def _city(city: Any, work_model: str, office: str) -> str:
    """The JD's city when no office could be picked for it, so the form can ask to check."""
    if office or work_model == WorkModel.REMOTE or city == Location.OTHER:
        return ""
    return Location(city).label if city in Location.values else ""


def _skills(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    skills: list[str] = []
    seen: set[str] = set()
    for item in value:
        skill = _squeeze(item).strip(" .-")
        if skill and len(skill) <= MAX_SKILL_LENGTH and skill.lower() not in seen:
            skills.append(skill)
            seen.add(skill.lower())
    return skills[:MAX_SKILLS]


def normalize_jd(raw: dict[str, Any], offices: list[OfficeRef]) -> dict[str, Any]:
    """Return the full JD field contract, dropping any value that would not fit.

    Bounty and function are never part of it: the bounty is the company's call and the
    server derives the function from title and skills.
    """
    if raw.get("is_job_description") is False:
        raw = {}
    why = _paragraphs(raw.get("why_we_need_you"))
    work_model = _choice(raw.get("work_model"), WorkModel.values)
    salary_min, salary_max = _salary(raw.get("salary"))
    office = _office(raw.get("city"), work_model, offices)
    return {
        "title": _title(raw.get("title")),
        "why_we_need_you": why,
        "why_we_need_you_drafted": bool(why) and raw.get("why_we_need_you_drafted") is True,
        **_lists(raw),
        "salary_min": salary_min,
        "salary_max": salary_max,
        "pay_basis": _choice(raw.get("pay_basis"), PayBasis.values),
        "work_model": work_model,
        "office": office,
        "unmatched_city": _city(raw.get("city"), work_model, office),
        "skills": _skills(raw.get("skills")),
    }


def has_content(fields: dict[str, Any]) -> bool:
    """Whether a read found anything that looks like a job: a title or its duties."""
    return bool(fields["title"] or fields["roles"] or fields["qualifications"])
