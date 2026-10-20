from typing import cast

import pytest

from apps.companies.models import Company
from apps.companies.tests.factories import CompanyFactory
from apps.jobs.models import InviteStatus, Job, JobStatus
from apps.jobs.tests.factories import JobAssignmentFactory, JobInviteFactory
from apps.ops.queries import annotate_ops_jobs
from apps.ops.tests.helpers import queued_job

pytestmark = pytest.mark.django_db


def test_counts_do_not_join_invites_or_assignments() -> None:
    """Joined, three invites against two assignments is six rows per job; subqueries are not."""
    sql = str(annotate_ops_jobs(Job.objects.all()).query)

    assert "JOIN" in sql  # the company and the team, one row each
    assert "jobs_jobinvite" in sql
    assert "jobs_jobassignment" in sql
    assert 'JOIN "jobs_jobinvite"' not in sql
    assert 'JOIN "jobs_jobassignment"' not in sql


def test_counts_are_right_when_a_job_has_invites_and_assignments_both() -> None:
    job = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1, status=JobStatus.ACTIVE)
    JobInviteFactory.create_batch(3, job=job, status=InviteStatus.PENDING)
    JobInviteFactory.create_batch(2, job=job, status=InviteStatus.ACCEPTED)
    JobInviteFactory(job=job, status=InviteStatus.DECLINED)
    JobAssignmentFactory.create_batch(4, job=job)
    quiet = queued_job(cast(Company, CompanyFactory()), posted_ago_days=1)

    rows = {row.pk: row for row in annotate_ops_jobs(Job.objects.all())}

    busy = rows[job.pk]
    assert (busy.invites_pending, busy.invites_accepted, busy.invites_declined) == (3, 2, 1)
    assert busy.recruiters_working == 4
    idle = rows[quiet.pk]
    assert (idle.invites_pending, idle.invites_accepted, idle.invites_declined) == (0, 0, 0)
    assert idle.recruiters_working == 0
