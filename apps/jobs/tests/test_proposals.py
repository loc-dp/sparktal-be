from collections.abc import Callable
from typing import Any, cast

import pytest
from django.core import mail
from django.http import Http404
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.candidates.models import Stage
from apps.candidates.tests.factories import ApplicationFactory
from apps.companies.tests.factories import OfficeFactory
from apps.jobs.access import NOT_YOURS
from apps.jobs.lifecycle import DELETE_BLOCKED_MESSAGE, JobRuleError, delete_job
from apps.jobs.models import ChangeKind, ChangeStatus, Job, JobChangeRequest, JobStatus
from apps.jobs.proposals import approve_change, propose_change, reject_change, withdraw_change
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db

Commit = Callable[[], Any]


def _posted(world: TeamWorld, **fields: Any) -> Job:
    """A job the draft-only TA created and sent, now live."""
    values: dict[str, Any] = {
        "company": world.company,
        "team": world.team_a,
        "created_by": world.ta_draft,
        "status": JobStatus.ACTIVE,
        "role_type": "new_position",
        "working_hours": "40h_mon_fri",
        "languages": ["en"],
    }
    return cast(Job, JobFactory(**{**values, **fields}))


def test_propose_edit_stores_only_given_keys(team_world: TeamWorld) -> None:
    job = _posted(team_world)
    request = propose_change(
        job,
        actor=team_world.ta_draft,
        kind=ChangeKind.EDIT,
        changes={"title": "Staff Engineer", "intent": "post", "status": "closed", "bogus": 1},
    )
    assert request.changes == {"title": "Staff Engineer"}
    assert request.status == ChangeStatus.PENDING
    job.refresh_from_db()
    assert job.status == JobStatus.ACTIVE
    assert job.title != "Staff Engineer"


@pytest.mark.parametrize(
    "changes",
    [
        {},
        None,
        {"intent": "post", "team": "x", "status": "closed"},
        {"bounty_type": "fixed", "bounty_pct": 20, "bounty_amount": 5},
    ],
)
def test_propose_edit_without_editable_keys_is_400(
    team_world: TeamWorld, changes: dict[str, Any] | None
) -> None:
    with pytest.raises(ValidationError):
        propose_change(
            _posted(team_world), actor=team_world.ta_draft, kind=ChangeKind.EDIT, changes=changes
        )
    assert not JobChangeRequest.objects.exists()


def test_propose_edit_with_invalid_value_is_400(team_world: TeamWorld) -> None:
    with pytest.raises(ValidationError):
        propose_change(
            _posted(team_world),
            actor=team_world.ta_draft,
            kind=ChangeKind.EDIT,
            changes={"title": "  "},
        )


def test_new_proposal_replaces_pending_one(team_world: TeamWorld) -> None:
    job = _posted(team_world)
    first = propose_change(
        job, actor=team_world.ta_draft, kind=ChangeKind.EDIT, changes={"title": "One"}
    )
    second = propose_change(job, actor=team_world.ta_draft, kind=ChangeKind.CLOSE)
    first.refresh_from_db()
    assert first.status == ChangeStatus.WITHDRAWN
    assert second.status == ChangeStatus.PENDING
    assert JobChangeRequest.objects.filter(status=ChangeStatus.PENDING).count() == 1


def test_ta_with_publish_cannot_propose(team_world: TeamWorld) -> None:
    with pytest.raises(PermissionDenied):
        propose_change(team_world.job_a, actor=team_world.ta_publish, kind=ChangeKind.CLOSE)


def test_hidden_job_is_404_for_proposer(team_world: TeamWorld) -> None:
    with pytest.raises(Http404):
        propose_change(team_world.job_b, actor=team_world.ta_draft, kind=ChangeKind.CLOSE)


def test_propose_state_rules(team_world: TeamWorld) -> None:
    active = _posted(team_world)
    with pytest.raises(PermissionDenied, match=NOT_YOURS):
        propose_change(active, actor=team_world.ta_draft, kind=ChangeKind.REOPEN)
    with pytest.raises(JobRuleError, match=DELETE_BLOCKED_MESSAGE):
        propose_change(active, actor=team_world.ta_draft, kind=ChangeKind.DELETE)
    closed = _posted(team_world, status=JobStatus.CLOSED)
    with pytest.raises(PermissionDenied):
        propose_change(closed, actor=team_world.ta_draft, kind=ChangeKind.CLOSE)
    ApplicationFactory(job=closed, stage=Stage.INTERVIEW)
    with pytest.raises(JobRuleError, match=DELETE_BLOCKED_MESSAGE):
        propose_change(closed, actor=team_world.ta_draft, kind=ChangeKind.DELETE)


def test_propose_emails_every_admin(
    team_world: TeamWorld, django_capture_on_commit_callbacks: Any
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        propose_change(_posted(team_world), actor=team_world.ta_draft, kind=ChangeKind.CLOSE)
    assert [m.to for m in mail.outbox] == [[team_world.admin.email]]
    assert "/jobs/" in mail.outbox[0].body


def test_approve_edit_applies_changes(team_world: TeamWorld) -> None:
    job = _posted(team_world)
    request = propose_change(
        job, actor=team_world.ta_draft, kind=ChangeKind.EDIT, changes={"title": "Staff Engineer"}
    )
    approved = approve_change(request, actor=team_world.admin)
    assert approved.title == "Staff Engineer"
    assert approved.status == JobStatus.ACTIVE  # a plain edit, never a re-post
    request.refresh_from_db()
    assert request.status == ChangeStatus.APPROVED
    assert request.reviewed_by == team_world.admin
    assert request.reviewed_at is not None


def test_approve_revalidates_against_current_job(team_world: TeamWorld) -> None:
    job = _posted(team_world)
    office = OfficeFactory(company=team_world.company, position=1)
    request = propose_change(
        job, actor=team_world.ta_draft, kind=ChangeKind.EDIT, changes={"office": str(office.pk)}
    )
    office.delete()
    with pytest.raises(ValidationError):
        approve_change(request, actor=team_world.admin)
    request.refresh_from_db()
    assert request.status == ChangeStatus.PENDING


def test_second_decision_is_rejected(team_world: TeamWorld) -> None:
    request = propose_change(_posted(team_world), actor=team_world.ta_draft, kind=ChangeKind.CLOSE)
    approve_change(request, actor=team_world.admin)
    with pytest.raises(JobRuleError, match=r"This proposal was already decided\."):
        approve_change(request, actor=team_world.admin)
    with pytest.raises(JobRuleError, match=r"This proposal was already decided\."):
        reject_change(request, actor=team_world.admin, note="no")


@pytest.mark.parametrize(
    ("kind", "start", "end"),
    [
        (ChangeKind.CLOSE, JobStatus.ACTIVE, JobStatus.CLOSED),
        (ChangeKind.REOPEN, JobStatus.CLOSED, JobStatus.ACTIVE),
    ],
)
def test_approve_close_and_reopen(team_world: TeamWorld, kind: str, start: str, end: str) -> None:
    job = _posted(team_world, status=start)
    approved = approve_change(
        propose_change(job, actor=team_world.ta_draft, kind=kind), actor=team_world.admin
    )
    assert approved.status == end


def test_approve_delete_soft_deletes(team_world: TeamWorld) -> None:
    job = _posted(team_world, status=JobStatus.CLOSED)
    approve_change(
        propose_change(job, actor=team_world.ta_draft, kind=ChangeKind.DELETE),
        actor=team_world.admin,
    )
    job.refresh_from_db()
    assert job.deleted_at is not None


def test_only_admin_reviews(team_world: TeamWorld) -> None:
    request = propose_change(_posted(team_world), actor=team_world.ta_draft, kind=ChangeKind.CLOSE)
    with pytest.raises(PermissionDenied):
        approve_change(request, actor=team_world.ta_draft)
    with pytest.raises(Http404):
        approve_change(request, actor=team_world.hm_a)


def test_reject_needs_note_and_emails_requester(
    team_world: TeamWorld, django_capture_on_commit_callbacks: Any
) -> None:
    request = propose_change(_posted(team_world), actor=team_world.ta_draft, kind=ChangeKind.CLOSE)
    with pytest.raises(ValidationError) as caught:
        reject_change(request, actor=team_world.admin, note="  ")
    assert caught.value.detail == {"note": ["Tell them why."]}
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        rejected = reject_change(request, actor=team_world.admin, note="Not this quarter")
    assert rejected.status == ChangeStatus.REJECTED
    assert rejected.review_note == "Not this quarter"
    assert [m.to for m in mail.outbox] == [[team_world.ta_draft.email]]
    assert "Not this quarter" in mail.outbox[0].body


def test_no_decision_email_for_inactive_requester(
    team_world: TeamWorld, django_capture_on_commit_callbacks: Any
) -> None:
    request = propose_change(_posted(team_world), actor=team_world.ta_draft, kind=ChangeKind.CLOSE)
    team_world.ta_draft.is_active = False
    team_world.ta_draft.save(update_fields=["is_active"])
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        approve_change(request, actor=team_world.admin)
    assert mail.outbox == []


def test_only_requester_can_withdraw(team_world: TeamWorld) -> None:
    request = propose_change(_posted(team_world), actor=team_world.ta_draft, kind=ChangeKind.CLOSE)
    with pytest.raises(PermissionDenied):
        withdraw_change(request, actor=team_world.admin)
    withdrawn = withdraw_change(request, actor=team_world.ta_draft)
    assert withdrawn.status == ChangeStatus.WITHDRAWN
    with pytest.raises(JobRuleError):
        withdraw_change(request, actor=team_world.ta_draft)


def test_approve_close_on_changed_job_says_reject_instead(team_world: TeamWorld) -> None:
    job = _posted(team_world)
    request = propose_change(job, actor=team_world.ta_draft, kind=ChangeKind.CLOSE)
    Job.objects.filter(pk=job.pk).update(status=JobStatus.CLOSED)
    with pytest.raises(JobRuleError, match=r"This job has changed since the proposal\."):
        approve_change(request, actor=team_world.admin)
    request.refresh_from_db()
    assert request.status == ChangeStatus.PENDING
    reopen = propose_change(job, actor=team_world.ta_draft, kind=ChangeKind.REOPEN)
    Job.objects.filter(pk=job.pk).update(status=JobStatus.ACTIVE)
    with pytest.raises(JobRuleError, match="Reject it instead"):
        approve_change(reopen, actor=team_world.admin)


def test_deleting_a_job_withdraws_its_pending_proposals(team_world: TeamWorld) -> None:
    job = _posted(team_world, status=JobStatus.CLOSED)
    request = propose_change(
        job, actor=team_world.ta_draft, kind=ChangeKind.EDIT, changes={"title": "x"}
    )
    delete_job(job, actor=team_world.admin)
    request.refresh_from_db()
    assert request.status == ChangeStatus.WITHDRAWN


def test_approved_edit_email_names_the_saved_title(
    team_world: TeamWorld, django_capture_on_commit_callbacks: Any
) -> None:
    request = propose_change(
        _posted(team_world),
        actor=team_world.ta_draft,
        kind=ChangeKind.EDIT,
        changes={"title": "Fresh Title"},
    )
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        approve_change(request, actor=team_world.admin)
    assert "Fresh Title" in mail.outbox[0].subject


def test_approved_delete_email_links_to_the_list(
    team_world: TeamWorld, django_capture_on_commit_callbacks: Any
) -> None:
    job = _posted(team_world, status=JobStatus.CLOSED)
    request = propose_change(job, actor=team_world.ta_draft, kind=ChangeKind.DELETE)
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        approve_change(request, actor=team_world.admin)
    assert str(job.pk) not in mail.outbox[0].body
    assert "/jobs" in mail.outbox[0].body
