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


def test_seed_is_idempotent() -> None:
    call_command("seed_demo", "--force")
    call_command("seed_demo", "--force")

    assert Job.objects.filter(title="Senior Backend Engineer").count() == 1
