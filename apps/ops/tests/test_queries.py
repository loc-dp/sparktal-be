from typing import cast

import pytest

from apps.companies.models import Company
from apps.companies.tests.factories import CompanyFactory
from apps.jobs.models import Job, JobStatus
from apps.jobs.tests.factories import JobAssignmentFactory
from apps.ops.queries import annotate_ops_jobs
from apps.ops.tests.helpers import queued_job

pytestmark = pytest.mark.django_db


def test_counts_do_not_join_assignments() -> None:
    """A subquery per count, never a join that multiplies the job's row."""
    sql = str(annotate_ops_jobs(Job.objects.all()).query)

    assert "JOIN" in sql  # the company and the team, one row each
    assert "jobs_jobassignment" in sql
    assert 'JOIN "jobs_jobassignment"' not in sql


def test_counts_are_right_when_a_job_has_assignments() -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1, status=JobStatus.ACTIVE)
    JobAssignmentFactory.create_batch(4, job=job)
    quiet = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)

    rows = {row.pk: row for row in annotate_ops_jobs(Job.objects.all())}

    busy = rows[job.pk]
    assert busy.recruiters_working == 4
    idle = rows[quiet.pk]
    assert idle.recruiters_working == 0
