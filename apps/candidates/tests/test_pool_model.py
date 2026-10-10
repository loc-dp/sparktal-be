from datetime import date
from decimal import Decimal

import pytest
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction

from apps.candidates.models import Candidate
from apps.candidates.tests.factories import CandidateFactory
from apps.core.testing.pdf import make_pdf
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db


def test_a_pool_candidate_keeps_every_new_field(recruiter_profile: RecruiterProfile) -> None:
    candidate = CandidateFactory(
        owner=recruiter_profile,
        full_name="Trương Mỹ Hạnh",
        dob=date(1991, 6, 14),
        location="hcm",
        is_expat=False,
        languages=["Vietnamese", "English"],
        skills=["React", "TypeScript"],
        years_experience=7,
        current_salary=Decimal("52000000"),
        expected_salary=Decimal("62000000"),
        notes="Prefers hybrid in District 4.",
    )

    stored = Candidate.objects.get(pk=candidate.pk)

    assert stored.languages == ["Vietnamese", "English"]
    assert stored.skills == ["React", "TypeScript"]
    assert stored.expected_salary == Decimal("62000000")
    assert stored.years_experience == 7
    assert stored.updated_at is not None


def test_saving_fills_the_hash_columns(recruiter_profile: RecruiterProfile) -> None:
    candidate = CandidateFactory(
        owner=recruiter_profile,
        full_name="Phạm Minh Quân",
        dob=date(1991, 6, 14),
        email="Quan@Gmail.com",
        phone="0909172846",
    )

    assert len(candidate.email_hash) == 64
    assert len(candidate.phone_hash) == 64
    assert len(candidate.name_dob_hash) == 64


def test_changing_the_email_rewrites_its_hash(recruiter_profile: RecruiterProfile) -> None:
    candidate = CandidateFactory(owner=recruiter_profile, email="old@example.com")
    before = candidate.email_hash

    candidate.email = "new@example.com"
    candidate.save()

    assert candidate.email_hash != before
    assert Candidate.objects.get(pk=candidate.pk).email_hash == candidate.email_hash


def test_saving_builds_the_search_vector(recruiter_profile: RecruiterProfile) -> None:
    CandidateFactory(
        owner=recruiter_profile,
        full_name="Đỗ Hải Yến",
        current_title="Frontend Engineer · Shopee",
        skills=["React", "TypeScript"],
        notes="Interested in fintech.",
    )

    found = Candidate.objects.filter(search_vector="React")

    assert [row.full_name for row in found] == ["Đỗ Hải Yến"]
    assert Candidate.objects.filter(search_vector="fintech").count() == 1
    assert Candidate.objects.filter(search_vector="Kafka").count() == 0


def test_the_search_vector_follows_an_edit(recruiter_profile: RecruiterProfile) -> None:
    candidate = CandidateFactory(owner=recruiter_profile, skills=["React"])

    candidate.skills = ["Kotlin"]
    candidate.save()

    assert Candidate.objects.filter(search_vector="Kotlin").count() == 1
    assert Candidate.objects.filter(search_vector="React").count() == 0


def test_a_cv_file_rides_along_with_its_original_name(
    recruiter_profile: RecruiterProfile,
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile)

    candidate.cv_file.save("stored.pdf", ContentFile(make_pdf("CV")), save=False)
    candidate.cv_filename = "Truong-My-Hanh_CV_2026.pdf"
    candidate.save()

    stored = Candidate.objects.get(pk=candidate.pk)
    assert stored.cv_file.read().startswith(b"%PDF-")
    assert stored.cv_filename == "Truong-My-Hanh_CV_2026.pdf"


def test_a_candidate_without_contact_details_still_saves(
    recruiter_profile: RecruiterProfile,
) -> None:
    candidate = CandidateFactory(owner=recruiter_profile, email="", phone="", dob=None)

    assert candidate.email_hash == ""
    assert candidate.phone_hash == ""
    # No dob, so the name+dob hash has nothing to bind the name to.
    assert candidate.name_dob_hash == ""


@pytest.mark.parametrize(
    "values",
    [
        {"years_experience": 61},
        {"current_salary": Decimal("-1")},
        {"expected_salary": Decimal("-1")},
    ],
)
def test_the_database_refuses_numbers_out_of_range(
    recruiter_profile: RecruiterProfile, values: dict[str, object]
) -> None:
    # Admin, shell scripts and imports skip the serializer; the table still holds the line.
    with pytest.raises(IntegrityError), transaction.atomic():
        CandidateFactory(owner=recruiter_profile, **values)
