import pytest
from django.core.management import call_command

from apps.candidates.models import Application, Candidate, Stage
from apps.candidates.search import parse_boolean
from apps.candidates.seed import demo_identity

pytestmark = pytest.mark.django_db


def test_seed_fills_the_backend_pipeline() -> None:
    call_command("seed_demo", "--force")

    backend = Application.objects.filter(job__title="Senior Backend Engineer")
    assert backend.count() == 12
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

    assert Application.objects.filter(job__title="Senior Backend Engineer").count() == 12
    assert Application.objects.count() == 16
    assert Candidate.objects.count() == 22
    assert Application.objects.filter(is_duplicate=True).count() == 1


def test_the_pool_has_candidates_that_were_never_submitted() -> None:
    call_command("seed_demo", "--force")
    pool = Candidate.objects.filter(applications__isnull=True)
    assert pool.count() == 6
    assert pool.filter(owner__nickname="NorthStar Talent").count() == 4
    assert pool.filter(is_expat=True).count() == 1


def test_the_pool_answers_the_product_boolean_example() -> None:
    call_command("seed_demo", "--force")
    query = parse_boolean('( "Frontend" OR "Fullstack" ) AND "React" NOT "Junior"')
    names = Candidate.objects.filter(
        owner__nickname="NorthStar Talent", search_vector=query
    ).values_list("full_name", flat=True)
    assert sorted(names) == ["Ngô Tuấn Kiệt", "Đỗ Hải Yến"]


def test_every_pool_candidate_has_identity_hashes() -> None:
    call_command("seed_demo", "--force")
    pool = Candidate.objects.filter(applications__isnull=True)
    assert all(row.email_hash and row.phone_hash and row.name_dob_hash for row in pool)


def test_seeding_twice_does_not_duplicate_the_pool() -> None:
    call_command("seed_demo", "--force")
    call_command("seed_demo", "--force")
    assert Candidate.objects.count() == 22


def test_the_duplicate_pair_is_real() -> None:
    call_command("seed_demo", "--force")
    duplicate = Application.objects.get(is_duplicate=True)
    assert duplicate.recruiter.nickname == "Bluefin Search"
    assert duplicate.candidate.full_name == "Phạm Minh Quân"
    assert duplicate.duplicate_of is not None
    assert duplicate.duplicate_of.recruiter.nickname == "NorthStar Talent"
    assert duplicate.duplicate_of.submitted_at < duplicate.submitted_at


def test_seed_identity_and_assessment_are_stable() -> None:
    call_command("seed_demo", "--force")
    before = Candidate.objects.get(full_name="Lê Thu Trang").email_hash
    call_command("seed_demo", "--force")
    assert Candidate.objects.get(full_name="Lê Thu Trang").email_hash == before
    assert demo_identity("Lê Thu Trang") == demo_identity("Lê Thu Trang")
    assert all(
        a.reason_for_leaving and a.motivation and a.consultant_note
        for a in Application.objects.all()
    )
