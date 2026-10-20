from typing import cast

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.companies.members import (
    ACCOUNT_LOCKED,
    EMAIL_TAKEN,
    LAST_ADMIN,
    TEAM_REQUIRED,
    UNSET,
    MemberRuleError,
    invite_member,
    remove_member,
    set_locked,
    update_member,
)
from apps.companies.models import Company, CompanyMembership, CompanyRole
from apps.companies.tests.factories import CompanyMembershipFactory, TeamFactory
from apps.jobs.models import ChangeKind, ChangeStatus, Job, JobChangeRequest, JobStatus
from apps.jobs.tests.factories import JobFactory

pytestmark = pytest.mark.django_db


def invite(company: Company, **kw: object) -> CompanyMembership:
    base: dict[str, object] = {
        "email": "new@velapay.vn",
        "full_name": "New",
        "role": "talent_acquisition",
    }
    return invite_member(company, **{**base, **kw})


def test_invite_ta_requires_team(company: Company) -> None:
    with pytest.raises(ValidationError) as exc:
        invite(company)
    assert exc.value.detail == {"team": [TEAM_REQUIRED]}
    assert not User.objects.filter(email="new@velapay.vn").exists()


def test_invite_ta_with_team_and_can_publish(company: Company) -> None:
    team = TeamFactory(company=company)
    m = invite(company, team=team, can_publish=True)
    assert m.team == team
    assert m.can_publish is True


def test_invite_admin_drops_team_and_can_publish(company: Company) -> None:
    m = invite(company, role="admin", team=TeamFactory(company=company), can_publish=True)
    assert m.team is None
    assert m.can_publish is False


def test_invite_hm_never_gets_can_publish(company: Company) -> None:
    m = invite(company, role="hiring_manager", team=TeamFactory(company=company), can_publish=True)
    assert m.can_publish is False


def removed_member(company: Company, *, locked: bool) -> User:
    seat = cast(
        CompanyMembership,
        CompanyMembershipFactory(company=company, role=CompanyRole.ADMIN, user__email="gone@x.vn"),
    )
    keeper = CompanyMembershipFactory(company=company, role=CompanyRole.ADMIN).user
    if locked:
        set_locked(seat, locked=True)
    remove_member(seat, actor=keeper)
    return User.objects.get(pk=seat.user_id)


def test_invite_of_a_removed_locked_user_says_what_the_caller_asks(company: Company) -> None:
    user = removed_member(company, locked=True)

    with pytest.raises(ValidationError) as default:
        invite(company, email=user.email, role="admin")
    with pytest.raises(ValidationError) as ops:
        invite(company, email=user.email, role="admin", locked_detail=ACCOUNT_LOCKED)

    # By default nobody learns of the lock; Sparktal's own portal asks to be told.
    assert default.value.detail == {"email": [EMAIL_TAKEN]}
    assert ops.value.detail == {"email": [ACCOUNT_LOCKED]}
    user.refresh_from_db()
    assert user.is_active is False
    assert user.locked_at is not None


def test_invite_locks_the_existing_user_row(company: Company) -> None:
    user = removed_member(company, locked=False)

    with CaptureQueriesContext(connection) as queries:
        invite(company, email=user.email, role="admin")

    table = User._meta.db_table
    reads = [q["sql"] for q in queries.captured_queries if q["sql"].startswith("SELECT")]
    assert any(f'FROM "{table}"' in sql and "FOR UPDATE" in sql for sql in reads)
    user.refresh_from_db()
    assert user.is_active is True


def test_update_member_to_admin_clears_team_and_can_publish(company: Company) -> None:
    m = CompanyMembershipFactory(
        company=company, role=CompanyRole.TALENT_ACQUISITION, can_publish=True
    )
    update_member(m, role=CompanyRole.ADMIN)
    m.refresh_from_db()
    assert m.team is None
    assert m.can_publish is False


def test_update_member_ta_to_hm_resets_can_publish(company: Company) -> None:
    m = CompanyMembershipFactory(
        company=company, role=CompanyRole.TALENT_ACQUISITION, can_publish=True
    )
    update_member(m, role=CompanyRole.HIRING_MANAGER)
    m.refresh_from_db()
    assert m.can_publish is False
    assert m.team is not None


def test_update_member_keeps_can_publish_when_role_stays_ta(company: Company) -> None:
    m = CompanyMembershipFactory(
        company=company, role=CompanyRole.TALENT_ACQUISITION, can_publish=True
    )
    update_member(m, role=CompanyRole.TALENT_ACQUISITION)
    m.refresh_from_db()
    assert m.can_publish is True


def test_update_member_sets_can_publish_explicitly(company: Company) -> None:
    m = CompanyMembershipFactory(company=company, role=CompanyRole.TALENT_ACQUISITION)
    update_member(m, can_publish=True)
    m.refresh_from_db()
    assert m.can_publish is True
    update_member(m, can_publish=False)
    m.refresh_from_db()
    assert m.can_publish is False


def test_update_member_can_publish_ignored_for_non_ta(company: Company) -> None:
    m = CompanyMembershipFactory(company=company, role=CompanyRole.HIRING_MANAGER)
    update_member(m, can_publish=True)
    m.refresh_from_db()
    assert m.can_publish is False


def test_admin_to_ta_requires_team(company: Company, company_admin: User) -> None:
    CompanyMembershipFactory(company=company, role=CompanyRole.ADMIN)  # second admin
    m = CompanyMembership.objects.get(user=company_admin)
    with pytest.raises(ValidationError) as exc:
        update_member(m, role=CompanyRole.TALENT_ACQUISITION)
    assert exc.value.detail == {"team": [TEAM_REQUIRED]}
    update_member(m, role=CompanyRole.TALENT_ACQUISITION, team=TeamFactory(company=company))
    m.refresh_from_db()
    assert m.role == CompanyRole.TALENT_ACQUISITION
    assert m.team is not None


def test_update_member_cannot_clear_team_of_hm(company: Company) -> None:
    m = CompanyMembershipFactory(company=company, role=CompanyRole.HIRING_MANAGER)
    with pytest.raises(ValidationError) as exc:
        update_member(m, team=None)
    assert exc.value.detail == {"team": [TEAM_REQUIRED]}


def test_update_member_keeps_last_admin_rule(company_admin: User) -> None:
    m = CompanyMembership.objects.get(user=company_admin)
    with pytest.raises(MemberRuleError) as exc:
        update_member(m, role=CompanyRole.HIRING_MANAGER, team=TeamFactory(company=m.company))
    assert exc.value.detail == LAST_ADMIN


def test_unset_leaves_team_alone(company: Company) -> None:
    m = CompanyMembershipFactory(company=company, role=CompanyRole.HIRING_MANAGER)
    team = m.team
    update_member(m, team=UNSET)
    m.refresh_from_db()
    assert m.team == team


def test_moving_member_takes_private_drafts_along(company: Company) -> None:
    old, new = TeamFactory(company=company), TeamFactory(company=company)
    m = CompanyMembershipFactory(company=company, role=CompanyRole.TALENT_ACQUISITION, team=old)
    other = CompanyMembershipFactory(company=company, role=CompanyRole.TALENT_ACQUISITION, team=old)
    draft = JobFactory(company=company, team=old, created_by=m.user, status=JobStatus.DRAFT)
    submitted = JobFactory(company=company, team=old, created_by=m.user, status=JobStatus.SUBMITTED)
    active = JobFactory(company=company, team=old, created_by=m.user, status=JobStatus.ACTIVE)
    others_draft = JobFactory(
        company=company, team=old, created_by=other.user, status=JobStatus.DRAFT
    )

    update_member(m, team=new)

    def team_of(job: Job) -> object:
        job.refresh_from_db()
        return job.team

    assert team_of(draft) == new
    assert team_of(submitted) == new
    assert team_of(active) == old
    assert team_of(others_draft) == old


def test_removing_member_withdraws_their_proposals(company: Company, job: Job) -> None:
    requester = cast(User, job.created_by)
    membership = CompanyMembership.objects.get(user=requester)
    admin = CompanyMembershipFactory(company=company, role=CompanyRole.ADMIN).user
    mine = JobChangeRequest.objects.create(
        job=job, requested_by=requester, kind=ChangeKind.EDIT, changes={"title": "x"}
    )
    decided = JobChangeRequest.objects.create(
        job=job,
        requested_by=requester,
        kind=ChangeKind.EDIT,
        changes={},
        status=ChangeStatus.APPROVED,
    )

    remove_member(membership, actor=admin)

    mine.refresh_from_db()
    decided.refresh_from_db()
    assert mine.status == ChangeStatus.WITHDRAWN
    assert decided.status == ChangeStatus.APPROVED


@pytest.mark.parametrize("change", ["team", "role", "none"])
def test_updating_member_withdraws_proposals_only_on_team_or_role_change(
    company: Company, job: Job, change: str
) -> None:
    team = job.team
    other_team = TeamFactory(company=company)
    requester = cast(User, job.created_by)
    membership = CompanyMembership.objects.get(user=requester)
    request = JobChangeRequest.objects.create(
        job=job, requested_by=requester, kind=ChangeKind.CLOSE
    )
    if change == "team":
        update_member(membership, team=other_team)
    elif change == "role":
        update_member(membership, role=CompanyRole.HIRING_MANAGER)
    else:
        update_member(membership, team=team, can_publish=True)
    request.refresh_from_db()
    expected = ChangeStatus.PENDING if change == "none" else ChangeStatus.WITHDRAWN
    assert request.status == expected
