from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import pytest
from django.core import mail
from django.core.management import call_command

from apps.chat.models import Channel
from apps.jobs.access import jobs_open_to
from apps.jobs.bounty import has_bounty
from apps.jobs.models import (
    BountyType,
    Job,
    JobAssignment,
    JobChangeRequest,
    JobStatus,
)
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db

CaptureCommits = Callable[..., AbstractContextManager[list[Callable[[], Any]]]]


def test_seed_creates_the_ten_vela_pay_jobs() -> None:
    call_command("seed_demo", "--force")

    jobs = Job.objects.filter(company__name="Vela Pay")
    assert jobs.count() == 10
    assert jobs.filter(status=JobStatus.ACTIVE).count() == 6
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
    assert Job.objects.filter(company__name="Vela Pay").count() == 10


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


MANAGER = "Growth Marketing Manager"
ANALYST = "Growth Analyst"


def test_seed_has_two_growth_jobs_by_thu() -> None:
    call_command("seed_demo", "--force")

    for title in (MANAGER, ANALYST):
        job = Job.objects.get(title=title)
        assert job.created_by is not None
        assert job.created_by.email == "thu.vo@velapay.vn"
        assert job.team is not None
        assert job.team.name == "Growth"
        assert job.status == JobStatus.ACTIVE
        assert has_bounty(job)
        assert job.deleted_at is None
    analyst = Job.objects.get(title=ANALYST)
    assert (analyst.bounty_type, analyst.bounty_pct) == (BountyType.PCT, 15)
    assert not Job.objects.filter(title__icontains="invite").exists()


def test_growth_analyst_is_assigned_to_bluefin_search_only() -> None:
    call_command("seed_demo", "--force")

    team = JobAssignment.objects.filter(job__title=ANALYST)
    assert [a.recruiter.user.email for a in team] == ["bluefin@example.com"]
    assert team[0].left_at is None
    assert Channel.objects.filter(
        job__title=ANALYST, recruiter__user__email="bluefin@example.com"
    ).exists()

    northstar = RecruiterProfile.objects.get(user__email="northstar@example.com")
    assert not jobs_open_to(northstar).filter(title=ANALYST).exists()
    assert jobs_open_to(northstar).filter(title=MANAGER).exists()


def test_every_live_demo_job_has_a_recruiter() -> None:
    call_command("seed_demo", "--force")

    live = Job.objects.filter(company__name="Vela Pay", status=JobStatus.ACTIVE)
    assert live.count() == 6
    unstaffed = live.exclude(assignments__left_at__isnull=True)
    assert list(unstaffed.values_list("title", flat=True)) == []


def test_seed_makes_no_marketplace_job() -> None:
    call_command("seed_demo", "--force")

    assert Job.objects.filter(on_marketplace=True).count() == 0


def test_seed_has_one_removed_assignment_with_a_cv() -> None:
    from apps.candidates.models import Application

    call_command("seed_demo", "--force")

    removed = JobAssignment.objects.filter(left_at__isnull=False)
    assert [(a.recruiter.nickname, a.job.title) for a in removed] == [
        ("NorthStar Talent", "Compliance Manager")
    ]
    gone = removed[0]
    assert gone.left_at is not None
    assert gone.left_at > gone.joined_at
    assert Application.objects.filter(job=gone.job, recruiter=gone.recruiter).exists()
    # The job still has a headhunter besides the removed one.
    assert JobAssignment.objects.filter(job=gone.job, left_at__isnull=True).exists()


def test_seed_sends_no_assignment_email(
    django_capture_on_commit_callbacks: CaptureCommits,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        call_command("seed_demo", "--force")

    assert mail.outbox == []


def test_seed_pending_job_has_no_bounty() -> None:
    call_command("seed_demo", "--force")

    pending = Job.objects.get(company__name="Vela Pay", status=JobStatus.PENDING)
    assert pending.title == "Head of Finance"
    assert (pending.bounty_type, pending.bounty_pct, pending.bounty_amount) == (None, None, None)
    assert not has_bounty(pending)
    assert pending.on_marketplace is False
    assert pending.posted_at is not None
    assert not JobAssignment.objects.filter(job=pending).exists()


def test_seed_company_drafts_have_no_bounty_either() -> None:
    call_command("seed_demo", "--force")

    unposted = Job.objects.filter(
        company__name="Vela Pay", status__in=(JobStatus.DRAFT, JobStatus.SUBMITTED)
    )
    assert unposted.count() == 2
    assert not any(has_bounty(job) for job in unposted)


def _business_rows() -> dict[str, int]:
    models_ = (Job, JobAssignment, JobChangeRequest, Channel)
    return {model.__name__: model.objects.count() for model in models_}


def test_seed_run_twice_adds_no_rows() -> None:
    call_command("seed_demo", "--force")
    first = _business_rows()

    call_command("seed_demo", "--force")

    assert _business_rows() == first
    # 9 held + 1 removed assignment, one chat channel each.
    assert first["JobAssignment"] == 10


def test_seed_jobs_carry_the_date_the_company_posted_them() -> None:
    call_command("seed_demo", "--force")

    posted = Job.objects.filter(
        company__name="Vela Pay",
        status__in=(JobStatus.PENDING, JobStatus.ACTIVE, JobStatus.CLOSED),
    )
    assert posted.count() == 8
    assert not posted.filter(posted_at__isnull=True).exists()
    assert (
        not Job.objects.filter(
            company__name="Vela Pay", status__in=(JobStatus.DRAFT, JobStatus.SUBMITTED)
        )
        .exclude(posted_at__isnull=True)
        .exists()
    )


def test_seed_adds_questions_from_northstar_and_sends_no_mail() -> None:
    from apps.jobs.models import JobQuestion

    call_command("seed_demo", "--force")
    call_command("seed_demo", "--force")

    backend = JobQuestion.objects.filter(job__title="Senior Backend Engineer")
    assert backend.count() == 3
    assert backend.exclude(answer="").count() == 2
    assert backend.filter(answer="").count() == 1
    analyst = JobQuestion.objects.get(job__title="Data Analyst")
    assert analyst.answer == ""
    assert JobQuestion.objects.count() == 4
    assert set(JobQuestion.objects.values_list("asked_by__nickname", flat=True)) == {
        "NorthStar Talent"
    }
    answered = backend.exclude(answer="")
    for q in answered:
        assert q.answered_by is not None
        assert q.answered_at is not None
        assert q.answered_at > q.created_at
    assert mail.outbox == []
