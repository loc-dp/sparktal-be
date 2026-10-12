import pytest
from django.core.management import call_command

from apps.accounts.models import User, UserKind
from apps.recruiters.models import RecruiterProfile, RecruiterStatus

pytestmark = pytest.mark.django_db


def test_seed_marks_atlas_pending() -> None:
    call_command("seed_demo", force=True)
    assert (
        RecruiterProfile.objects.get(nickname="NorthStar Talent").status == RecruiterStatus.APPROVED
    )
    assert RecruiterProfile.objects.get(nickname="Atlas Hunters").status == RecruiterStatus.PENDING


def test_reset_removes_e2e_signup_accounts() -> None:
    from apps.recruiters.seed import reset

    User.objects.create_user(
        email="signup-1@e2e.example.com",
        password="pass-1234-word",
        full_name="E2E Signup",
        kind=UserKind.RECRUITER,
    )
    reset()
    assert not User.objects.filter(email__endswith="@e2e.example.com").exists()


def test_seed_includes_a_rejected_recruiter_with_a_review_note() -> None:
    call_command("seed_demo", force=True)
    quasar = RecruiterProfile.objects.get(nickname="Quasar Sourcing")
    assert quasar.status == RecruiterStatus.REJECTED
    assert quasar.review_note
    assert quasar.reviewed_by is not None
    assert quasar.reviewed_at is not None


def test_seeded_expertise_uses_values_the_profile_form_accepts() -> None:
    from apps.recruiters.serializers import clean_expertise

    call_command("seed_demo", force=True)
    for profile in RecruiterProfile.objects.all():
        assert clean_expertise(profile.expertise) == profile.expertise
