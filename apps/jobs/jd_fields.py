"""Clean JD fields from Claude so every value fits the Create new job form."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from apps.companies.models import Location
from apps.jobs.models import PayBasis, WorkModel

MAX_TITLE_LENGTH = 120
MAX_WHY_LENGTH = 1500
# Company forms list 50+ duties once KPI goals are counted; the form takes them all.
MAX_ITEMS = 60
MAX_ITEM_LENGTH = 600
MAX_SKILLS = 20
MAX_SKILL_LENGTH = 40
MAX_PLACE_LENGTH = 80
# A monthly salary below 1 million VND is a figure in millions Claude did not expand ("15").
MIN_MONTHLY_VND = 1_000_000
MAX_MONTHLY_VND = 10_000_000_000
VND_SPELLINGS = {"vnd", "vnđ", "₫", "đ", "dong", "đồng"}
# A yearly figure becomes a monthly one rounded to 100,000 VND, the way people quote it.
MONTHLY_ROUNDING = 100_000
LIST_FIELDS = ("roles", "qualifications", "nice_to_have", "benefits")
# Bullets, check marks and numbering ("1.", "2)", "a.") that a JD puts before an item.
BULLET = re.compile(r"^(?:[•●○◦▪▫■□►▸✓✔➢➤\-–—*+·]|(?:\d{1,2}|[a-zA-Z])[.)](?=\s))\s*")
# Layout leftovers at the end of an item: a list separator ("Lập kế hoạch;"), dots after
# its full stop ("theo qui định. …."), a space before its full stop ("nhân viên .").
TRAILING = re.compile(r"\s*[;,]+$|(?<=\.)\s+[.…]+$|\s+(?=[.…]+$)")
SENTENCE_BREAK = re.compile(r"[.!?;:\n]+")


@dataclass(frozen=True)
class OfficeRef:
    id: str
    city: str
    place: str = ""


def _squeeze(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _capped(value: Any, limit: int) -> str:
    """Squeezed onto one line and cut at a word boundary."""
    text = _squeeze(value)
    if len(text) <= limit:
        return text
    return text[: limit + 1].rsplit(" ", 1)[0]


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
            item = TRAILING.sub("", BULLET.sub("", _squeeze(line), count=1).strip())
            item = item[:MAX_ITEM_LENGTH]
            if item and item.lower() not in seen:
                items.append(item)
                seen.add(item.lower())
    return items[:MAX_ITEMS]


def _lists(raw: dict[str, Any]) -> dict[str, list[str]]:
    lists = {field: _items(raw.get(field)) for field in LIST_FIELDS}
    optional = {item.lower() for item in lists["nice_to_have"]}
    lists["qualifications"] = [
        item for item in lists["qualifications"] if item.lower() not in optional
    ]
    return lists


def _letters(text: str) -> str:
    """Letters and digits only: what is left of the JD's words once Claude repairs a layout."""
    return "".join(ch for ch in unicodedata.normalize("NFC", text).casefold() if ch.isalnum())


def _found(text: str, page: str) -> bool:
    """Whether every sentence of text is in page (a source already run through _letters)."""
    parts = [part for part in (_letters(part) for part in SENTENCE_BREAK.split(text)) if part]
    return bool(parts) and all(part in page for part in parts)


def _drafted(why: str, claimed: Any, roles: list[str], source: str) -> bool:
    """Whether the form should warn that Claude wrote why_we_need_you.

    Claude's own flag is unreliable both ways: it flags a paragraph it copied and passes a
    reworded one as copied. The JD's text decides instead, once most duties are found in it:
    a PDF's text layer can differ from the pages Claude read.
    """
    if not why:
        return False
    page = _letters(source)
    readable = bool(roles) and 2 * sum(_found(role, page) for role in roles) >= len(roles)
    if not readable:
        return claimed is True
    return not _found(why, page)


def _amount(value: Any, yearly: bool) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if yearly:
        value = round(value / 12 / MONTHLY_ROUNDING) * MONTHLY_ROUNDING
    return value if MIN_MONTHLY_VND <= value <= MAX_MONTHLY_VND else None


def _salary(value: Any) -> tuple[int | None, int | None]:
    """A monthly or yearly VND range fills the form, as monthly; USD or odd figures stay blank."""
    if not isinstance(value, dict):
        return None, None
    currency = _squeeze(value.get("currency")).lower()
    if currency not in VND_SPELLINGS or value.get("period") not in ("month", "year", None):
        return None, None
    yearly = value.get("period") == "year"
    low, high = _amount(value.get("minimum"), yearly), _amount(value.get("maximum"), yearly)
    if value.get("minimum") is not None and low is None:
        return None, None
    if value.get("maximum") is not None and high is None:
        return None, None
    if low is not None and high is not None and high < low:
        return None, None
    return low, high


def _choice(value: Any, choices: list[str]) -> str:
    return value if isinstance(value, str) and value in choices else ""


def _same_place(office: str, place: Any) -> bool:
    """Whether a JD's place is an office's: "Shibuya, Tokyo" is in "Tokyo, Japan", and so is
    "Japan"."""
    if not isinstance(place, str) or not place.strip():
        return False
    office_city, jd_city = office.casefold(), place.casefold()
    return office_city.split(",")[0].strip() in jd_city or (
        jd_city.split(",")[-1].strip() in office_city
    )


def _office(city: Any, place: Any, work_model: str, offices: list[OfficeRef]) -> str:
    """The company's only office in that city; no guess when there are none or several.
    Offices abroad share one code, so there the JD's place has to name the office's."""
    if work_model == WorkModel.REMOTE or city == Location.OTHER:
        return ""
    matches = [office for office in offices if office.city == city]
    if city == Location.ABROAD:
        matches = [office for office in matches if _same_place(office.place, place)]
    return matches[0].id if len(matches) == 1 else ""


def _city(city: Any, place: Any, work_model: str, office: str) -> str:
    """Where the JD puts the job when no office could be picked for it, so the form can ask
    to check: the city's label, or the place as written when it has no code."""
    if office or work_model == WorkModel.REMOTE:
        return ""
    if city in Location.values and city not in (Location.OTHER, Location.ABROAD):
        return Location(city).label
    return _capped(place, MAX_PLACE_LENGTH)


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


def normalize_jd(raw: dict[str, Any], offices: list[OfficeRef], source: str = "") -> dict[str, Any]:
    """Return the full JD field contract, dropping any value that would not fit.

    `source` is the JD's text, used to check what Claude says it copied. Bounty and function
    are never part of it: the bounty is the company's call and the server derives the
    function from title and skills.
    """
    if raw.get("is_job_description") is False:
        raw = {}
    why = _paragraphs(raw.get("why_we_need_you"))
    lists = _lists(raw)
    drafted = _drafted(why, raw.get("why_we_need_you_drafted"), lists["roles"], source)
    work_model = _choice(raw.get("work_model"), WorkModel.values)
    salary_min, salary_max = _salary(raw.get("salary"))
    office = _office(raw.get("city"), raw.get("location"), work_model, offices)
    return {
        "title": _capped(raw.get("title"), MAX_TITLE_LENGTH),
        "why_we_need_you": why,
        "why_we_need_you_drafted": drafted,
        **{field: "\n".join(items) for field, items in lists.items()},
        "salary_min": salary_min,
        "salary_max": salary_max,
        "pay_basis": _choice(raw.get("pay_basis"), PayBasis.values),
        "work_model": work_model,
        "office": office,
        "unmatched_city": _city(raw.get("city"), raw.get("location"), work_model, office),
        "skills": _skills(raw.get("skills")),
    }


def has_content(fields: dict[str, Any]) -> bool:
    """Whether a read found anything that looks like a job: a title or its duties."""
    return bool(fields["title"] or fields["roles"] or fields["qualifications"])
