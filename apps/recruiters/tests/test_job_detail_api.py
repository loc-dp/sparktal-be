"""`GET /api/v1/recruiters/me/jobs/<id>`: a headhunter reads a job they hold or have a CV on."""

from collections.abc import Callable
from typing import Any, cast

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.chat.models import Channel
from apps.jobs.models import BountyType, Job, JobAssignment, JobStatus
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db


def url(job: Job) -> str:
    return f"/api/v1/recruiters/me/jobs/{job.pk}"


def remove(recruiter: RecruiterProfile, job: Job) -> None:
    JobAssignment.objects.filter(job=job, recruiter=recruiter).update(left_at=timezone.now())


def cv_on(job: Job, recruiter: RecruiterProfile) -> Application:
    return cast(
        Application,
        ApplicationFactory(
            job=job, candidate=CandidateFactory(owner=recruiter), recruiter=recruiter
        ),
    )


@pytest.fixture
def assigned_live_job(recruiter_profile: RecruiterProfile) -> Job:
    job = cast(
        Job,
        JobFactory(
            on_marketplace=False,
            guarantee_days=60,
            note_for_recruiters="No candidates from MoMo, please.",
        ),
    )
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    return job


@pytest.fixture
def assigned_closed_job(recruiter_profile: RecruiterProfile) -> Job:
    job = cast(Job, JobFactory(status=JobStatus.CLOSED, closed_at=timezone.now()))
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    return job


@pytest.fixture
def removed_job(recruiter_profile: RecruiterProfile) -> Job:
    job = cast(Job, JobFactory())
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    remove(recruiter_profile, job)
    return job


@pytest.fixture
def removed_with_cv_job(removed_job: Job, recruiter_profile: RecruiterProfile) -> Job:
    cv_on(removed_job, recruiter_profile)
    return removed_job


def test_assigned_recruiter_reads_job_with_bounty(
    recruiter_client: APIClient, assigned_live_job: Job, recruiter_profile: RecruiterProfile
) -> None:
    channel = Channel.objects.get(
        job=assigned_live_job, recruiter=recruiter_profile, application=None
    )
    cv_on(assigned_live_job, recruiter_profile)

    response = recruiter_client.get(url(assigned_live_job))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(assigned_live_job.pk)
    assert body["bounty_type"] == BountyType.PCT
    assert body["bounty_pct"] == 18
    assert body["guarantee_days"] == 60
    assert body["roles"] == assigned_live_job.roles
    assert body["note_for_recruiters"] == "No candidates from MoMo, please."
    assert body["company"]["name"] == assigned_live_job.company.name
    assert set(body["company"]) == {"id", "name", "industry"}
    assert body["assigned"] is True
    assert body["joined_at"] is not None
    assert body["submitted_count"] == 1
    assert body["active_count"] == 1
    assert body["channel_id"] == str(channel.pk)


def test_channel_id_is_the_general_thread_not_a_candidate_thread(
    recruiter_client: APIClient, assigned_live_job: Job, recruiter_profile: RecruiterProfile
) -> None:
    general = Channel.objects.get(job=assigned_live_job, recruiter=recruiter_profile)
    candidate_threads = [cv_on(assigned_live_job, recruiter_profile) for _ in range(2)]

    body = recruiter_client.get(url(assigned_live_job)).json()

    assert Channel.objects.filter(application__in=candidate_threads).count() == 2
    assert body["channel_id"] == str(general.pk)


def test_channel_id_is_null_without_a_channel(
    recruiter_client: APIClient, assigned_live_job: Job, recruiter_profile: RecruiterProfile
) -> None:
    Channel.objects.filter(job=assigned_live_job, recruiter=recruiter_profile).delete()

    body = recruiter_client.get(url(assigned_live_job)).json()

    assert body["channel_id"] is None


def test_channel_id_ignores_another_recruiters_channel(
    recruiter_client: APIClient, assigned_live_job: Job, recruiter_profile: RecruiterProfile
) -> None:
    Channel.objects.filter(job=assigned_live_job, recruiter=recruiter_profile).delete()
    other = JobAssignmentFactory(job=assigned_live_job)

    body = recruiter_client.get(url(assigned_live_job)).json()

    assert Channel.objects.filter(job=assigned_live_job, recruiter=other.recruiter).exists()
    assert body["channel_id"] is None


def test_counts_only_this_recruiters_cvs(
    recruiter_client: APIClient, assigned_live_job: Job, recruiter_profile: RecruiterProfile
) -> None:
    cv_on(assigned_live_job, recruiter_profile)
    rejected = cv_on(assigned_live_job, recruiter_profile)
    rejected.stage = Stage.REJECTED
    rejected.save(update_fields=["stage"])
    rival = JobAssignmentFactory(job=assigned_live_job).recruiter
    cv_on(assigned_live_job, rival)

    body = recruiter_client.get(url(assigned_live_job)).json()

    assert (body["submitted_count"], body["active_count"]) == (2, 1)


def test_job_detail_costs_a_fixed_number_of_queries(
    recruiter_client: APIClient, assigned_live_job: Job, recruiter_profile: RecruiterProfile
) -> None:
    def count() -> int:
        with CaptureQueriesContext(connection) as queries:
            assert recruiter_client.get(url(assigned_live_job)).status_code == 200
        return len(queries)

    count()  # warm the session and content-type caches
    before = count()
    for _ in range(3):
        cv_on(assigned_live_job, recruiter_profile)

    assert count() == before


def test_closed_job_is_still_readable(
    recruiter_client: APIClient, assigned_closed_job: Job
) -> None:
    response = recruiter_client.get(url(assigned_closed_job))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "closed"
    assert body["closed_at"] is not None
    assert body["assigned"] is True


def test_removed_with_cv_reads_job(recruiter_client: APIClient, removed_with_cv_job: Job) -> None:
    response = recruiter_client.get(url(removed_with_cv_job))

    assert response.status_code == 200
    body = response.json()
    assert body["assigned"] is False
    assert body["joined_at"] is None
    assert body["submitted_count"] == 1


def test_removed_without_cv_gets_404(recruiter_client: APIClient, removed_job: Job) -> None:
    assert recruiter_client.get(url(removed_job)).status_code == 404


@pytest.mark.parametrize("on_marketplace", [False, True])
def test_never_assigned_gets_404(
    recruiter_client: APIClient, on_marketplace: bool, settings: Any
) -> None:
    settings.MARKETPLACE_ENABLED = on_marketplace
    live_job = JobFactory(on_marketplace=on_marketplace)

    assert recruiter_client.get(url(live_job)).status_code == 404


def test_job_assigned_to_someone_else_gets_404(
    recruiter_client: APIClient, assigned_live_job: Job
) -> None:
    other = JobAssignmentFactory(job=JobFactory())

    assert recruiter_client.get(url(other.job)).status_code == 404


def test_company_user_is_refused_without_a_body(
    client_for: Callable[[User], APIClient], company_admin: User, joined_job: Job
) -> None:
    response = client_for(company_admin).get(url(joined_job))

    assert response.status_code in (403, 404)
    assert "bounty" not in response.content.decode()
    assert joined_job.title not in response.content.decode()


def test_pending_recruiter_is_refused(
    client_for: Callable[[User], APIClient], pending_recruiter_user: User, joined_job: Job
) -> None:
    assert client_for(pending_recruiter_user).get(url(joined_job)).status_code == 403


def test_anonymous_is_refused(api_client: APIClient, joined_job: Job) -> None:
    assert api_client.get(url(joined_job)).status_code in (401, 403)
