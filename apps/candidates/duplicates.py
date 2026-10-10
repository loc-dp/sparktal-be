import calendar
import hashlib
import re
import unicodedata
from datetime import datetime
from typing import TYPE_CHECKING

import phonenumbers
from django.db.models import Q
from django.utils import timezone

if TYPE_CHECKING:
    from apps.candidates.models import Application, Candidate
    from apps.companies.models import Company

DEFAULT_WINDOW_MONTHS = 12
HASH_FIELDS = ("email_hash", "phone_hash", "name_dob_hash")


def normalise_name(value: str) -> str:
    folded = value.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFKD", folded)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", plain).strip().lower()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def identity_hashes(candidate: "Candidate") -> dict[str, str]:
    email = (candidate.email or "").strip().lower()
    phone = ""
    if candidate.phone:
        try:
            parsed = phonenumbers.parse(str(candidate.phone), "VN")
            if phonenumbers.is_possible_number(parsed):
                phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            pass
    name = normalise_name(candidate.full_name or "")
    name_dob = f"{name}|{candidate.dob.isoformat()}" if name and candidate.dob else ""
    return {
        "email_hash": _digest(email),
        "phone_hash": _digest(phone),
        "name_dob_hash": _digest(name_dob),
    }


def window_start(months: int = DEFAULT_WINDOW_MONTHS, *, now: datetime | None = None) -> datetime:
    moment = now if now is not None else timezone.now()
    year_shift, month_index = divmod(moment.month - 1 - months, 12)
    year = moment.year + year_shift
    month = month_index + 1
    return moment.replace(
        year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1])
    )


def find_duplicate(
    company: "Company", candidate: "Candidate", *, within_months: int = DEFAULT_WINDOW_MONTHS
) -> "Application | None":
    from apps.candidates.models import Application

    hashes = identity_hashes(candidate)
    clauses = [
        Q(**{f"candidate__{field}": hashes[field]}) for field in HASH_FIELDS if hashes[field]
    ]
    if not clauses:
        return None
    matches = clauses[0]
    for clause in clauses[1:]:
        matches |= clause
    return (
        Application.objects.filter(
            job__company=company, submitted_at__gte=window_start(within_months)
        )
        .filter(matches)
        .order_by("submitted_at", "id")
        .first()
    )
