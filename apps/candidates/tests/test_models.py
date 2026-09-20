import pytest
from django.db import IntegrityError

from apps.candidates.models import Stage
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.companies.models import Company
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db


def test_application_carries_a_cv_file(company: Company) -> None:
    application = ApplicationFactory(job=JobFactory(company=company))

    assert application.cv_filename
    with application.cv_file.open("rb") as handle:
        assert handle.read(5) == b"%PDF-"


def test_a_candidate_applies_to_a_job_once(company: Company) -> None:
    job = JobFactory(company=company)
    candidate = CandidateFactory()
    ApplicationFactory(job=job, candidate=candidate)

    with pytest.raises(IntegrityError):
        ApplicationFactory(job=job, candidate=candidate)


def test_changing_the_stage_stamps_stage_changed_at(company: Company) -> None:
    application = ApplicationFactory(job=JobFactory(company=company), stage=Stage.NEW)
    before = application.stage_changed_at

    application.move_to(Stage.SCREEN)

    application.refresh_from_db()
    assert application.stage == Stage.SCREEN
    assert application.stage_changed_at > before
    assert application.history.count() == 2


def test_rejecting_stores_the_reason(company: Company) -> None:
    application = ApplicationFactory(job=JobFactory(company=company))

    application.reject("Not enough Go experience")

    application.refresh_from_db()
    assert application.stage == Stage.REJECTED
    assert application.rejected_reason == "Not enough Go experience"
