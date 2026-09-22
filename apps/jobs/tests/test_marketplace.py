from collections.abc import Callable

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.companies.models import Location
from apps.companies.tests.factories import CompanyFactory, OfficeFactory
from apps.jobs.access import can_submit_to_job, jobs_joined_by
from apps.jobs.marketplace import MarketplaceFilterSet, annotate_marketplace, marketplace_queryset
from apps.jobs.models import JobFunction, JobStatus, WorkModel
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db


def filtered(data: dict[str, str], recruiter: RecruiterProfile) -> list[str]:
    queryset = annotate_marketplace(marketplace_queryset(recruiter), recruiter)
    filterset = MarketplaceFilterSet(data=data, queryset=queryset)
    assert filterset.is_valid(), filterset.errors
    return [job.title for job in filterset.qs]


def test_marketplace_includes_every_company_but_only_active_jobs(
    recruiter_profile: RecruiterProfile,
) -> None:
    JobFactory(company=CompanyFactory(name="Vela Pay"))
    JobFactory(company=CompanyFactory(name="Lumen Retail"))
    JobFactory(status=JobStatus.PENDING)
    assert marketplace_queryset(recruiter_profile).count() == 2
    assert marketplace_queryset(None).count() == 0


def test_annotations_count_only_own_submissions_and_active_assignments(
    recruiter_profile: RecruiterProfile,
) -> None:
    job = JobFactory()
    mine = JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    JobAssignmentFactory(job=job)
    gone = JobAssignmentFactory(job=job)
    gone.left_at = timezone.now()
    gone.save(update_fields=["left_at"])
    ApplicationFactory(job=job, candidate=CandidateFactory(owner=recruiter_profile))
    ApplicationFactory(job=job)
    row = annotate_marketplace(marketplace_queryset(recruiter_profile), recruiter_profile).get()
    assert row.joined
    assert row.submitted_count == 1
    assert row.recruiters_working == 2
    assert list(jobs_joined_by(recruiter_profile)) == [job]
    assert can_submit_to_job(recruiter_profile, job)
    mine.left_at = timezone.now()
    mine.save(update_fields=["left_at"])
    assert not can_submit_to_job(recruiter_profile, job)
    assert list(jobs_joined_by(recruiter_profile)) == []


def test_filters_cover_search_function_location_work_model_and_joined(
    recruiter_profile: RecruiterProfile,
) -> None:
    hanoi = CompanyFactory(name="Ha Noi Co")
    OfficeFactory(company=hanoi, city=Location.HANOI, position=0)
    backend = JobFactory(title="Senior Backend Engineer", skills=["Go", "Kafka"])
    JobFactory(title="Controller", company=hanoi, function=JobFunction.FINANCE)
    JobFactory(title="Remote SRE", work_model=WorkModel.REMOTE)
    JobAssignmentFactory(job=backend, recruiter=recruiter_profile)
    assert filtered({"search": "kafka"}, recruiter_profile) == [backend.title]
    assert filtered({"search": "Ha Noi Co"}, recruiter_profile) == ["Controller"]
    assert filtered({"function": "finance"}, recruiter_profile) == ["Controller"]
    assert filtered({"location": Location.HANOI}, recruiter_profile) == ["Controller"]
    assert filtered({"work_model": "remote"}, recruiter_profile) == ["Remote SRE"]
    assert filtered({"joined": "true"}, recruiter_profile) == [backend.title]
    assert sorted(filtered({"joined": "false"}, recruiter_profile)) == ["Controller", "Remote SRE"]


def test_other_recruiter_identity_is_not_exposed(
    client_for: Callable[[User], APIClient], recruiter_user: User
) -> None:
    job = JobFactory()
    other = RecruiterProfileFactory(nickname="Bluefin Search", user__email="bluefin@example.com")
    JobAssignmentFactory(job=job, recruiter=other)
    payload = client_for(recruiter_user).get("/api/v1/marketplace/jobs").content.decode()
    assert "Bluefin Search" not in payload
    assert "bluefin@example.com" not in payload
