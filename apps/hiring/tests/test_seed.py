import pytest
from django.core.management import call_command

from apps.candidates.models import Application
from apps.hiring.models import ApprovalRequest, ApprovalStatus, CandidateNote, Feedback, Interview
from apps.jobs.access import can_view_job

pytestmark = pytest.mark.django_db


def test_seed_adds_hiring_activity_to_the_backend_job() -> None:
    call_command("seed_demo", force=True)
    quan = Application.objects.get(
        job__title="Senior Backend Engineer",
        candidate__full_name="Phạm Minh Quân",
        is_duplicate=False,
    )
    assert list(quan.interviews.values_list("round", flat=True)) == [1, 2]
    assert Feedback.objects.filter(interview__application=quan).count() == 2
    assert quan.company_notes.count() == 2
    assert quan.messages.count() >= 3
    approval = ApprovalRequest.objects.get(status=ApprovalStatus.PENDING)
    assert approval.application.candidate.full_name == "Lê Thu Trang"
    assert approval.requested_by is not None
    assert approval.requested_by.email == "maianh.tran@velapay.vn"
    assert approval.approver.email == "huy.phan@velapay.vn"
    assert can_view_job(approval.approver, approval.application.job)


def test_hiring_seed_is_idempotent() -> None:
    call_command("seed_demo", force=True)
    counts = (
        Interview.objects.count(),
        Feedback.objects.count(),
        CandidateNote.objects.count(),
        ApprovalRequest.objects.count(),
    )
    call_command("seed_demo", force=True)
    assert counts == (
        Interview.objects.count(),
        Feedback.objects.count(),
        CandidateNote.objects.count(),
        ApprovalRequest.objects.count(),
    )


def test_seed_covers_every_verdict_and_approval_status() -> None:
    call_command("seed_demo", force=True)
    assert set(Feedback.objects.values_list("verdict", flat=True)) == {
        "strong_yes",
        "yes",
        "no",
        "strong_no",
    }
    assert set(ApprovalRequest.objects.values_list("status", flat=True)) == {
        ApprovalStatus.PENDING,
        ApprovalStatus.APPROVED,
        ApprovalStatus.DECLINED,
        ApprovalStatus.CANCELLED,
    }
    decided = ApprovalRequest.objects.exclude(status=ApprovalStatus.PENDING)
    assert all(approval.decided_at is not None for approval in decided)


def test_seed_adds_interviews_outside_the_backend_job() -> None:
    call_command("seed_demo", force=True)
    assert Interview.objects.exclude(application__job__title="Senior Backend Engineer").exists()


def test_seed_lets_the_hiring_manager_named_in_the_brief_see_his_jobs() -> None:
    from apps.accounts.models import User
    from apps.jobs.models import Job

    call_command("seed_demo", force=True)
    khoa = User.objects.get(email="khoa.nguyen@velapay.vn")
    for title in ("Senior Backend Engineer", "Data Analyst"):
        assert can_view_job(khoa, Job.objects.get(title=title))


def test_feedback_is_dated_after_its_interview() -> None:
    call_command("seed_demo", force=True)
    for feedback in Feedback.objects.select_related("interview"):
        assert feedback.created_at > feedback.interview.ends_at
