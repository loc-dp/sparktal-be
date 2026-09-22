import uuid

import pytest
from django.core.files.base import ContentFile

from apps.candidates.models import CvParse, CvParseStatus
from apps.candidates.tasks import parse_cv
from apps.candidates.tests.factories import CvParseFactory
from apps.core.testing.pdf import make_pdf
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db

CV = """Truong My Hanh
Payments Backend Lead

Email: myhanh.truong@gmail.com
Phone: 0909 172 846

Summary
9 years of experience building payment systems.

Skills
Go, Kafka, PostgreSQL
"""


def test_a_readable_pdf_fills_the_six_contract_keys(
    recruiter_profile: RecruiterProfile,
) -> None:
    parse = CvParseFactory(owner=recruiter_profile, file=ContentFile(make_pdf(CV), name="cv.pdf"))

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.status == CvParseStatus.DONE
    assert parse.error == ""
    assert parse.finished_at is not None
    assert set(parse.fields) == {
        "full_name",
        "current_title",
        "email",
        "phone",
        "skills",
        "years_experience",
    }
    assert parse.fields["full_name"] == "Truong My Hanh"
    assert parse.fields["email"] == "myhanh.truong@gmail.com"
    assert parse.fields["skills"] == ["Go", "Kafka", "PostgreSQL"]
    assert parse.fields["years_experience"] == 9


def test_an_unreadable_file_still_finishes_done_with_empty_fields(
    recruiter_profile: RecruiterProfile,
) -> None:
    parse = CvParseFactory(
        owner=recruiter_profile, file=ContentFile(b"%PDF-1.4 truncated", name="cv.pdf")
    )

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.status == CvParseStatus.DONE
    assert parse.fields["full_name"] == ""
    assert parse.fields["skills"] == []


def test_a_missing_blob_fails_with_a_readable_message(
    recruiter_profile: RecruiterProfile,
) -> None:
    parse = CvParseFactory(owner=recruiter_profile, file=ContentFile(make_pdf(CV), name="cv.pdf"))
    parse.file.delete(save=True)

    parse_cv(str(parse.pk))

    parse.refresh_from_db()
    assert parse.status == CvParseStatus.FAILED
    assert parse.error == "We couldn't read that CV. Fill the form in yourself."
    assert parse.finished_at is not None


def test_an_unknown_id_is_ignored(recruiter_profile: RecruiterProfile) -> None:
    parse_cv(str(uuid.uuid4()))  # no exception: a retry must not crash the worker

    assert CvParse.objects.count() == 0
