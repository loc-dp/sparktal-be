import hashlib
from datetime import date

import pytest

from apps.candidates.duplicates import identity_hashes, normalise_name
from apps.candidates.tests.factories import CandidateFactory

pytestmark = pytest.mark.django_db


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_the_email_hash_ignores_case_and_padding() -> None:
    upper = CandidateFactory.build(email="  Quan.Pham@Gmail.COM ")
    lower = CandidateFactory.build(email="quan.pham@gmail.com")

    assert identity_hashes(upper)["email_hash"] == sha("quan.pham@gmail.com")
    assert identity_hashes(upper)["email_hash"] == identity_hashes(lower)["email_hash"]


def test_the_phone_hash_uses_the_e164_form() -> None:
    national = CandidateFactory.build(phone="0909172846")
    international = CandidateFactory.build(phone="+84909172846")

    assert identity_hashes(national)["phone_hash"] == sha("+84909172846")
    assert identity_hashes(national)["phone_hash"] == identity_hashes(international)["phone_hash"]


def test_the_name_hash_folds_diacritics_case_and_spacing() -> None:
    assert normalise_name("  Phạm   Minh  Quân ") == "pham minh quan"
    assert normalise_name("Đặng Khánh Vy") == "dang khanh vy"


def test_the_name_dob_hash_needs_both_halves() -> None:
    with_dob = CandidateFactory.build(full_name="Phạm Minh Quân", dob=date(1991, 6, 14))
    without_dob = CandidateFactory.build(full_name="Phạm Minh Quân", dob=None)

    assert identity_hashes(with_dob)["name_dob_hash"] == sha("pham minh quan|1991-06-14")
    assert identity_hashes(without_dob)["name_dob_hash"] == ""


def test_missing_values_hash_to_an_empty_string() -> None:
    blank = CandidateFactory.build(email="", phone="", full_name="", dob=None)

    assert identity_hashes(blank) == {
        "email_hash": "",
        "phone_hash": "",
        "name_dob_hash": "",
    }


def test_two_spellings_of_the_same_person_collide() -> None:
    typed = CandidateFactory.build(
        full_name="pham minh quan",
        dob=date(1991, 6, 14),
        email="QUAN@gmail.com",
        phone="0909172846",
    )
    parsed = CandidateFactory.build(
        full_name="Phạm Minh Quân",
        dob=date(1991, 6, 14),
        email="quan@gmail.com",
        phone="+84 909 172 846",
    )

    assert identity_hashes(typed) == identity_hashes(parsed)
