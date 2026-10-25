import pytest
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

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
    assert approval.approver.email == "khoa.nguyen@velapay.vn"


def test_the_seeded_approver_can_see_the_job() -> None:
    call_command("seed_demo", force=True)
    approval = ApprovalRequest.objects.get(status=ApprovalStatus.PENDING)
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


def test_seed_places_three_hires_in_each_guarantee_state() -> None:
    from apps.hiring.models import Placement
    from apps.hiring.placements import PayoutStatus, PlacementStatus, placement_state

    call_command("seed_demo", force=True)
    today = timezone.localdate()
    states = {
        placement.application.candidate.full_name: placement_state(placement, today)
        for placement in Placement.objects.select_related("application__candidate")
    }

    assert len(states) == 3
    assert (states["Phan Nhật Minh"].status, states["Phan Nhật Minh"].payout_status) == (
        PlacementStatus.PASSED,
        PayoutStatus.DUE,
    )
    assert states["Đinh Quỳnh Anh"].status == PlacementStatus.IN_GUARANTEE
    assert states["Cao Minh Khôi"].status == PlacementStatus.NO_SHOW
    assert {p.application.job.company.name for p in Placement.objects.all()} == {"Vela Pay"}
    offer = Application.objects.get(
        job__title="Senior Backend Engineer", candidate__full_name="Nguyễn Hoàng Anh"
    )
    assert offer.stage == "offer"
    assert not hasattr(offer, "placement")


def test_seeding_placements_twice_keeps_three_and_sends_no_mail() -> None:
    from apps.hiring.models import Placement

    call_command("seed_demo", force=True)
    call_command("seed_demo", force=True)

    assert Placement.objects.count() == 3
    assert mail.outbox == []


def test_reseeding_returns_a_paid_placement_to_due() -> None:
    from decimal import Decimal

    from apps.accounts.models import User
    from apps.hiring.models import Placement
    from apps.hiring.placements import PayoutStatus, placement_state

    call_command("seed_demo", force=True)
    placement = Placement.objects.get(application__candidate__full_name="Phan Nhật Minh")
    placement.paid_amount = Decimal("1800")
    placement.paid_at = timezone.localdate()
    placement.payout_note = "Transferred"
    placement.paid_by = User.objects.first()
    placement.save()

    call_command("seed_demo", force=True)

    placement.refresh_from_db()
    assert (placement.paid_amount, placement.paid_at, placement.payout_note) == (None, None, "")
    assert placement.paid_by is None
    assert placement_state(placement, timezone.localdate()).payout_status == PayoutStatus.DUE
