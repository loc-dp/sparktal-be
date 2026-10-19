from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, cast

import pytest
from django.core import mail
from django.core.management import call_command

from apps.chat.models import Channel
from apps.jobs.access import jobs_offered_to
from apps.jobs.bounty import has_bounty
from apps.jobs.models import (
    BountyType,
    InviteStatus,
    Job,
    JobAssignment,
    JobChangeRequest,
    JobInvite,
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
ANALYST = "Growth Analyst (invite only)"


def _invites(title: str) -> dict[str, str]:
    """Who was invited to the job, by login email, with the invite's status."""
    return {
        invite.recruiter.user.email: invite.status
        for invite in JobInvite.objects.filter(job__title=title).select_related("recruiter__user")
    }


def test_seed_has_direct_only_jobs_with_invites() -> None:
    call_command("seed_demo", "--force")

    for title in (MANAGER, ANALYST):
        job = Job.objects.get(title=title)
        assert job.created_by is not None
        assert job.created_by.email == "thu.vo@velapay.vn"
        assert job.team is not None
        assert job.team.name == "Growth"
        assert job.status == JobStatus.ACTIVE
        assert job.on_marketplace is False
        assert has_bounty(job)
        assert job.deleted_at is None
    analyst = Job.objects.get(title=ANALYST)
    assert (analyst.bounty_type, analyst.bounty_pct) == (BountyType.PCT, 15)

    assert _invites(MANAGER) == {
        "bluefin@example.com": InviteStatus.PENDING,
        "northstar@example.com": InviteStatus.ACCEPTED,
    }
    assert _invites(ANALYST) == {"bluefin@example.com": InviteStatus.PENDING}
    # The accepted invite has its assignment; nobody else is on a direct-only job.
    manager_team = JobAssignment.objects.filter(job__title=MANAGER)
    assert [a.recruiter.user.email for a in manager_team] == ["northstar@example.com"]
    assert manager_team[0].left_at is None
    assert not JobAssignment.objects.filter(job__title=ANALYST).exists()
    assert Channel.objects.filter(
        job__title=MANAGER, recruiter__user__email="northstar@example.com"
    ).exists()

    bluefin = RecruiterProfile.objects.get(user__email="bluefin@example.com")
    northstar = RecruiterProfile.objects.get(user__email="northstar@example.com")
    assert set(jobs_offered_to(bluefin).filter(title__in=(MANAGER, ANALYST))) == set(
        Job.objects.filter(title__in=(MANAGER, ANALYST))
    )
    assert [
        job.title for job in jobs_offered_to(northstar).filter(title__in=(MANAGER, ANALYST))
    ] == [MANAGER]


def test_seed_invites_come_from_sparktal_staff() -> None:
    call_command("seed_demo", "--force")

    inviters = {
        invite.invited_by.email
        for invite in JobInvite.objects.select_related("invited_by")
        if invite.invited_by
    }
    assert inviters == {"admin@sparktal.vn"}


def test_seed_puts_every_other_live_job_on_the_marketplace() -> None:
    call_command("seed_demo", "--force")

    direct_only = {MANAGER, ANALYST}
    live = Job.objects.filter(
        company__name="Vela Pay", status__in=(JobStatus.ACTIVE, JobStatus.CLOSED)
    )
    assert {job.title for job in live.filter(on_marketplace=False)} == direct_only
    assert live.filter(on_marketplace=True).count() == live.count() - 2
    # Nothing waiting on the company or on Sparktal is on a channel yet.
    unlive = Job.objects.filter(company__name="Vela Pay").exclude(pk__in=live)
    assert not unlive.filter(on_marketplace=True).exists()


def test_seed_pending_job_has_no_bounty() -> None:
    call_command("seed_demo", "--force")

    pending = Job.objects.get(company__name="Vela Pay", status=JobStatus.PENDING)
    assert pending.title == "Head of Finance"
    assert (pending.bounty_type, pending.bounty_pct, pending.bounty_amount) == (None, None, None)
    assert not has_bounty(pending)
    assert pending.on_marketplace is False
    assert pending.posted_at is not None
    assert not JobInvite.objects.filter(job=pending).exists()


def test_seed_company_drafts_have_no_bounty_either() -> None:
    call_command("seed_demo", "--force")

    unposted = Job.objects.filter(
        company__name="Vela Pay", status__in=(JobStatus.DRAFT, JobStatus.SUBMITTED)
    )
    assert unposted.count() == 2
    assert not any(has_bounty(job) for job in unposted)


def test_seed_sends_no_invite_email(django_capture_on_commit_callbacks: CaptureCommits) -> None:
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        call_command("seed_demo", "--force")

    assert mail.outbox == []
    assert JobInvite.objects.count() == 3
    assert callbacks  # the invites did queue their notifier; it was a no-op


def test_the_seed_holds_back_only_the_invite_email(
    django_capture_on_commit_callbacks: CaptureCommits,
) -> None:
    from django.db import transaction

    from apps.accounts.tests.factories import UserFactory
    from apps.jobs.distribution import invite_recruiters
    from apps.jobs.seed import _without_invite_email
    from apps.jobs.tests.factories import JobFactory
    from apps.recruiters.tests.factories import RecruiterProfileFactory

    ops = UserFactory(is_staff=True)
    job = JobFactory()
    quiet = cast(RecruiterProfile, RecruiterProfileFactory(user__email="quiet@example.com"))
    loud = cast(RecruiterProfile, RecruiterProfileFactory(user__email="loud@example.com"))
    fired: list[str] = []

    with django_capture_on_commit_callbacks(execute=True):
        transaction.on_commit(lambda: fired.append("before"))
        with _without_invite_email():
            transaction.on_commit(lambda: fired.append("inside"))
            invite_recruiters(job, actor=ops, recruiters=[quiet])
        transaction.on_commit(lambda: fired.append("after"))
        invite_recruiters(job, actor=ops, recruiters=[loud])  # outside the block: mailed

    assert fired == ["before", "inside", "after"]
    assert [message.to for message in mail.outbox] == [["loud@example.com"]]
    assert JobInvite.objects.filter(job=job).count() == 2


def _business_rows() -> dict[str, int]:
    models_ = (Job, JobInvite, JobAssignment, JobChangeRequest, Channel)
    return {model.__name__: model.objects.count() for model in models_}


def test_seed_run_twice_adds_no_rows() -> None:
    call_command("seed_demo", "--force")
    first = _business_rows()
    invite_ids = set(JobInvite.objects.values_list("pk", flat=True))

    call_command("seed_demo", "--force")

    assert _business_rows() == first
    assert set(JobInvite.objects.values_list("pk", flat=True)) == invite_ids
    assert _invites(MANAGER)["northstar@example.com"] == InviteStatus.ACCEPTED


def test_seed_brings_back_an_invite_that_was_declined() -> None:
    call_command("seed_demo", "--force")
    JobInvite.objects.filter(job__title=ANALYST).update(status=InviteStatus.DECLINED)

    call_command("seed_demo", "--force")

    assert _invites(ANALYST) == {"bluefin@example.com": InviteStatus.PENDING}
    assert JobInvite.objects.filter(job__title=ANALYST).count() == 1


def test_seed_reset_deletes_the_invites_of_the_demo_jobs() -> None:
    from apps.recruiters.tests.factories import RecruiterProfileFactory

    call_command("seed_demo", "--force")
    first = _business_rows()
    # An invite from someone who is not a demo account, left on a demo job by a UI session.
    outsider = RecruiterProfileFactory(nickname="Outsider Search")
    JobInvite.objects.create(job=Job.objects.get(title=MANAGER), recruiter=outsider)

    call_command("seed_demo", "--reset", "--force")

    assert not JobInvite.objects.filter(recruiter=outsider).exists()
    assert _business_rows() == first
    assert _invites(MANAGER) == {
        "bluefin@example.com": InviteStatus.PENDING,
        "northstar@example.com": InviteStatus.ACCEPTED,
    }
    assert _invites(ANALYST) == {"bluefin@example.com": InviteStatus.PENDING}


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
