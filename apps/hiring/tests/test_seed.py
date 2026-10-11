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
