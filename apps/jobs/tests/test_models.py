import pytest

from apps.companies.models import Company
from apps.jobs.models import BountyType, Job, JobStatus, PayBasis, WorkModel
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db


def test_defaults_match_the_create_form(company: Company) -> None:
    job = Job.objects.create(company=company, title="Head of Finance", function="finance")

    assert job.status == JobStatus.PENDING
    assert job.pay_basis == PayBasis.GROSS
    assert job.work_model == WorkModel.ONSITE
    assert job.bounty_type == BountyType.PCT
    assert len(job.share_token) >= 20
    assert job.published_at is None


def test_share_tokens_are_unique(company: Company) -> None:
    tokens = {JobFactory(company=company).share_token for _ in range(5)}

    assert len(tokens) == 5


def test_history_is_recorded(company: Company) -> None:
    job = JobFactory(company=company, title="Data Analyst")
    job.title = "Senior Data Analyst"
    job.save()

    assert job.history.count() == 2


def test_publish_and_close_transitions(company: Company) -> None:
    job = JobFactory(company=company, status=JobStatus.PENDING, published_at=None)

    job.publish()

    assert job.status == JobStatus.ACTIVE
    assert job.published_at is not None

    job.close()

    assert job.status == JobStatus.CLOSED
    assert job.closed_at is not None

    job.reopen()

    assert job.status == JobStatus.ACTIVE
    assert job.closed_at is None


def test_location_label(company: Company) -> None:
    remote = JobFactory(company=company, work_model=WorkModel.REMOTE, office=None)
    onsite = JobFactory(
        company=company, work_model=WorkModel.ONSITE, office=company.offices.first()
    )

    assert remote.location_label == "Remote (Vietnam)"
    assert onsite.location_label == "Ho Chi Minh City"
