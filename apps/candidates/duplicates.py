import hashlib
import re
import unicodedata
from typing import TYPE_CHECKING

import phonenumbers

if TYPE_CHECKING:
    from apps.candidates.models import Candidate


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
