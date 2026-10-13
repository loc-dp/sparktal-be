"""Clean CV fields from any parser so every value fits its Candidate column."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import phonenumbers
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from apps.candidates.parsing import MAX_SKILL_LENGTH, MAX_SKILLS, MAX_YEARS
from apps.companies.models import Location

MAX_NAME_LENGTH = 120
MAX_TITLE_LENGTH = 160
MAX_LANGUAGES = 10
MAX_LANGUAGE_LENGTH = 24
MIN_AGE = 16
MAX_AGE = 80
MAX_SALARY = 10**15 - 1
CONTACT_KEYS = ("email", "phone")
JOB_DATE = re.compile(r"(?P<year>\d{4})(?:-(?P<month>\d{1,2}))?")
EARLIEST_JOB_YEAR = 1950
# A model sometimes packs two contacts into one value: "a@x.com / b@y.com".
CONTACT_SEPARATOR = re.compile(r"[,;/|]| or ")


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _name(value: Any) -> str:
    words = _text(value, MAX_NAME_LENGTH).split(" ")
    # Title-case a shouted name word by word, so "(Vicky)" or "Ph.D." survive;
    # a lone capitalised word ("MBA") is an acronym, not shouting.
    if sum(word.isupper() for word in words) < 2:
        return " ".join(words)
    return " ".join(word.title() if word.isupper() else word for word in words)


def _email(value: Any, source_text: str) -> str:
    # PDF text layers break lines inside an address ("gmail.co⏎m") and turn unmapped
    # ligatures into NUL ("ty\x00any"), so ground without whitespace or control characters.
    source = re.sub(r"[\s\x00-\x1f]+", "", source_text).lower()
    for email in CONTACT_SEPARATOR.split(_text(value, 512).lower()):
        email = email.strip()
        try:
            validate_email(email)
        except ValidationError:
            continue
        if not source_text or email in source:
            return email
    return ""


def _phone(value: Any, source_text: str) -> str:
    digits = re.sub(r"\D", "", source_text)
    for raw in CONTACT_SEPARATOR.split(_text(value, 64)):
        try:
            number = phonenumbers.parse(raw.strip(), "VN")
        except phonenumbers.NumberParseException:
            continue
        if not phonenumbers.is_valid_number(number):
            continue
        if not source_text or str(number.national_number) in digits:
            return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    return ""


def _dob(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        born = date.fromisoformat(value.strip())
    except ValueError:
        return None
    return born.isoformat() if is_working_age(born) else None


def is_working_age(born: date) -> bool:
    today = timezone.localdate()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return MIN_AGE <= age <= MAX_AGE


def _location(value: Any) -> str:
    return value if isinstance(value, str) and value in Location.values else ""


def _clip(value: str, length: int) -> str:
    """Cut at the last whole word that fits, so a long skill never ends mid-word."""
    if len(value) <= length:
        return value
    head = value[: length + 1].rsplit(" ", 1)[0]
    return head if head != value[: length + 1] else value[:length]


def _tags(value: Any, length: int, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in value:
        tag = _clip(_text(item, 512), length).strip(" .-&,/(")
        if tag and tag.lower() not in seen:
            tags.append(tag)
            seen.add(tag.lower())
    return tags[:limit]


def _whole(value: Any, ceiling: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, int) and 0 <= value <= ceiling:
        return value
    return None


def _month(value: Any) -> tuple[int, bool] | None:
    """'2019-05' or '2019' as (month count, whether a month was given); a bare year is January."""
    if not isinstance(value, str):
        return None
    match = JOB_DATE.fullmatch(value.strip())
    if match is None:
        return None
    year, month = int(match["year"]), int(match["month"] or 1)
    if year < EARLIEST_JOB_YEAR or not 1 <= month <= 12:
        return None
    return year * 12 + month - 1, match["month"] is not None


def _period(job: Any, now: int) -> tuple[int, int] | None:
    """A job as [start, end) in months. "Nov 2017 - May 2019" includes May, as CVs mean it;
    a bare end year stays exclusive so "2015 - 2017" is two years, not three."""
    if not isinstance(job, dict):
        return None
    start = _month(job.get("start"))
    if job.get("end") == "present":
        end = now + 1
    else:
        until = _month(job.get("end"))
        if until is None:
            return None
        end = until[0] + 1 if until[1] else until[0]
    if start is None or not start[0] < end <= now + 1:
        return None
    return start[0], end


def _years_from_jobs(jobs: Any) -> int | None:
    """Total years covered by the job periods, overlaps counted once; None without dates."""
    if not isinstance(jobs, list):
        return None
    today = timezone.localdate()
    now = today.year * 12 + today.month - 1
    periods = [period for job in jobs if (period := _period(job, now)) is not None]
    if not periods:
        return None
    covered = reach = 0
    for start, end in sorted(periods):
        covered += max(0, end - max(start, reach))
        reach = max(reach, end)
    return covered // 12


def _years(raw: dict[str, Any]) -> int | None:
    # Models misadd overlapping date ranges, so the total is computed here from the dates
    # Claude copied; a stated total ("8 years of experience") is only the fallback.
    computed = _years_from_jobs(raw.get("jobs"))
    if computed is not None:
        return computed if computed <= MAX_YEARS else None
    return _whole(raw.get("years_experience"), MAX_YEARS)


def normalize_fields(raw: dict[str, Any], source_text: str = "") -> dict[str, Any]:
    """Return the full CV field contract, dropping any value that would not fit.

    `source_text` is the file's text layer. When it is present, an email or phone
    that does not appear in it is treated as invented and dropped.
    """
    return {
        "full_name": _name(raw.get("full_name")),
        "current_title": _text(raw.get("current_title"), MAX_TITLE_LENGTH),
        "email": _email(raw.get("email"), source_text),
        "phone": _phone(raw.get("phone"), source_text),
        "dob": _dob(raw.get("dob")),
        "location": _location(raw.get("location")),
        "is_expat": raw.get("is_expat") is True,
        "languages": _tags(raw.get("languages"), MAX_LANGUAGE_LENGTH, MAX_LANGUAGES),
        "skills": _tags(raw.get("skills"), MAX_SKILL_LENGTH, MAX_SKILLS),
        "years_experience": _years(raw),
        "current_salary": _whole(raw.get("current_salary"), MAX_SALARY),
        "expected_salary": _whole(raw.get("expected_salary"), MAX_SALARY),
    }


def fill_contacts(
    primary: dict[str, Any], backup: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    """Replace an email or phone the gate rejected with the backup reader's find.

    Both sides must already be normalized: a primary value that failed the gate is empty here.
    `raw` is the primary reader's output before the gate. Where it gave no value at all, the
    CV has none of the candidate's own: the backup's find there is a referee's or a company's.
    """
    merged = dict(primary)
    for key in CONTACT_KEYS:
        if raw.get(key) and not merged.get(key) and backup.get(key):
            merged[key] = backup[key]
    return merged
