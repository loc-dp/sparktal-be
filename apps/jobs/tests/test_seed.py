import pytest
from django.core.management import call_command

from apps.jobs.models import Job, JobStatus

pytestmark = pytest.mark.django_db


def test_seed_creates_the_six_vela_pay_jobs() -> None:
    call_command("seed_demo", "--force")

    jobs = Job.objects.filter(company__name="Vela Pay")
    assert jobs.count() == 6
    assert jobs.filter(status=JobStatus.ACTIVE).count() == 4
    assert jobs.filter(status=JobStatus.PENDING).count() == 1
    assert jobs.filter(status=JobStatus.CLOSED).count() == 1

    backend = jobs.get(title="Senior Backend Engineer")
    assert backend.function == "engineering"
    assert backend.bounty_pct == 18
    assert backend.skills
    assert backend.office is not None


def test_seed_removes_jobs_that_are_not_part_of_the_demo() -> None:
    call_command("seed_demo", "--force")
    from apps.companies.models import Company

    company = Company.objects.get(name="Vela Pay")
    Job.objects.create(
        company=company,
        title="Left over from an E2E run",
        why_we_need_you="x",
        roles="x",
        qualifications="x",
    )

    call_command("seed_demo", "--reset", "--force")

    assert not Job.objects.filter(title="Left over from an E2E run").exists()
    assert Job.objects.filter(company__name="Vela Pay").count() == 6


def test_seed_clears_edits_made_through_the_ui() -> None:
    call_command("seed_demo", "--force")
    job = Job.objects.get(title="Senior Backend Engineer")
    job.note_for_recruiters = "Typed during an E2E run"
    job.save()

    call_command("seed_demo", "--force")

    job.refresh_from_db()
    assert job.note_for_recruiters == ""


def test_seed_is_idempotent() -> None:
    call_command("seed_demo", "--force")
    call_command("seed_demo", "--force")

    assert Job.objects.filter(title="Senior Backend Engineer").count() == 1


def test_seed_has_one_fixed_bounty_job() -> None:
    from apps.jobs.models import BountyType

    call_command("seed_demo", "--force")

    fixed = Job.objects.filter(company__name="Vela Pay", bounty_type=BountyType.FIXED)
    assert [job.title for job in fixed] == ["Compliance Manager"]
    assert fixed[0].bounty_amount == 120_000_000
    assert fixed[0].bounty_pct is None


def test_compliance_manager_sits_in_the_hanoi_office_its_brief_names() -> None:
    call_command("seed_demo", "--force")

    job = Job.objects.get(title="Compliance Manager")
    assert job.office is not None
    assert job.office.city == "hn"


def test_seeded_assignments_joined_after_the_job_went_live() -> None:
    from apps.jobs.models import JobAssignment

    call_command("seed_demo", "--force")

    for assignment in JobAssignment.objects.select_related("job"):
        published_at = assignment.job.published_at
        assert published_at is not None
        assert assignment.joined_at > published_at
