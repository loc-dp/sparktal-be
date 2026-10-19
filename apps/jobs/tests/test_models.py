import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import User
from apps.companies.models import Company, Location
from apps.companies.tests.factories import OfficeFactory
from apps.jobs.models import (
    ChangeKind,
    ChangeStatus,
    Job,
    JobChangeRequest,
    JobStatus,
    PayBasis,
    WorkModel,
)
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db


def test_defaults_match_the_create_form(company: Company) -> None:
    job = Job.objects.create(company=company, title="Head of Finance", function="finance")

    assert job.status == JobStatus.DRAFT
    assert job.pay_basis == PayBasis.GROSS
    assert job.work_model == WorkModel.ONSITE
    assert (job.bounty_type, job.bounty_pct, job.bounty_amount) == (None, None, None)
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


def test_location_label(company: Company) -> None:
    remote = JobFactory(company=company, work_model=WorkModel.REMOTE, office=None)
    onsite = JobFactory(
        company=company, work_model=WorkModel.ONSITE, office=company.offices.first()
    )

    assert remote.location_label == "Remote (Vietnam)"
    assert onsite.location_label == "Ho Chi Minh City"


def test_a_job_abroad_is_labelled_by_its_office_place(company: Company) -> None:
    office = OfficeFactory(company=company, city=Location.ABROAD, place="Singapore", position=1)
    job = JobFactory(company=company, work_model=WorkModel.HYBRID, office=office)

    assert job.location_label == "Singapore"


def test_new_job_defaults_to_draft(company: Company) -> None:
    job = Job.objects.create(company=company, title="Head of Finance")

    assert job.status == "draft"


def test_draft_may_omit_jd_body(company: Company) -> None:
    job = Job(company=company, title="Head of Finance", function="finance")

    job.full_clean(exclude=["share_token", "created_by"])


def test_one_pending_change_request_per_requester(job: Job, company_ta: User) -> None:
    JobChangeRequest.objects.create(job=job, requested_by=company_ta, kind=ChangeKind.CLOSE)

    with pytest.raises(IntegrityError), transaction.atomic():
        JobChangeRequest.objects.create(job=job, requested_by=company_ta, kind=ChangeKind.EDIT)

    JobChangeRequest.objects.update(status=ChangeStatus.REJECTED)
    JobChangeRequest.objects.create(job=job, requested_by=company_ta, kind=ChangeKind.EDIT)
    assert job.change_requests.filter(status=ChangeStatus.PENDING).count() == 1
