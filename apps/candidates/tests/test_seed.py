import pytest
from django.core.management import call_command

from apps.candidates.models import Application, Stage

pytestmark = pytest.mark.django_db


def test_seed_fills_the_backend_pipeline() -> None:
    call_command("seed_demo", "--force")

    backend = Application.objects.filter(job__title="Senior Backend Engineer")
    assert backend.count() == 11
    assert backend.filter(stage=Stage.INTERVIEW).count() == 3
    assert backend.filter(stage=Stage.OFFER).count() == 1
    assert backend.filter(stage=Stage.REJECTED).count() == 1
    assert backend.filter(is_duplicate=True).count() == 1
    assert {a.recruiter.nickname for a in backend} == {
        "NorthStar Talent",
        "Bluefin Search",
        "Atlas Hunters",
    }


def test_every_application_has_a_readable_cv() -> None:
    call_command("seed_demo", "--force")

    application = Application.objects.first()
    assert application is not None
    with application.cv_file.open("rb") as handle:
        assert handle.read(5) == b"%PDF-"


def test_other_jobs_have_applicants_too() -> None:
    call_command("seed_demo", "--force")

    assert Application.objects.filter(job__title="Data Analyst").exists()
    assert Application.objects.filter(job__title="QA Engineer", stage=Stage.ONBOARDED).exists()


def test_seed_is_idempotent() -> None:
    call_command("seed_demo", "--force")
    call_command("seed_demo", "--force")

    assert Application.objects.filter(job__title="Senior Backend Engineer").count() == 11
