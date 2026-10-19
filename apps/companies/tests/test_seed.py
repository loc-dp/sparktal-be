import pytest
from django.core.management import call_command

from apps.accounts.models import User
from apps.companies.models import Company, CompanyMembership, CompanyRole, Team
from apps.hiring.models import ApprovalRequest, CandidateNote, Feedback, Interview
from apps.jobs.access import can_view_job
from apps.jobs.models import ChangeKind, ChangeStatus, Job, JobChangeRequest, JobStatus
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db


def test_seed_creates_vela_pay_with_members_and_offices() -> None:
    call_command("seed_demo", "--force")

    company = Company.objects.get(name="Vela Pay")
    assert company.profile_completed is True
    assert company.industry == "fintech"
    assert company.offices.count() >= 1
    roles = {m.user.email: m.role for m in company.memberships.all()}
    assert roles["nhung.le@velapay.vn"] == CompanyRole.ADMIN
    assert roles["maianh.tran@velapay.vn"] == CompanyRole.TALENT_ACQUISITION
    assert roles["khoa.nguyen@velapay.vn"] == CompanyRole.HIRING_MANAGER


def test_seed_creates_four_recruiters_with_nicknames() -> None:
    call_command("seed_demo", "--force")

    assert set(RecruiterProfile.objects.values_list("nickname", flat=True)) == {
        "NorthStar Talent",
        "Bluefin Search",
        "Atlas Hunters",
        "Quasar Sourcing",
    }


def test_seed_creates_an_onboarding_company_for_e2e() -> None:
    call_command("seed_demo", "--force")

    company = Company.objects.get(name="Kestrel Logistics")
    assert company.profile_completed is False
    assert company.memberships.get(user__email="new.admin@example.com").role == CompanyRole.ADMIN


def test_seed_resets_the_onboarding_company() -> None:
    call_command("seed_demo", "--force")
    company = Company.objects.get(name="Kestrel Logistics")
    company.offices.create(address="Left over from a previous run", city="hp", position=0)
    company.industry = "fintech"
    company.mark_profile_completed()

    call_command("seed_demo", "--force")

    company.refresh_from_db()
    assert company.offices.count() == 0
    assert company.industry == ""
    assert company.profile_completed is False


def test_seed_is_idempotent() -> None:
    call_command("seed_demo", "--force")
    call_command("seed_demo", "--force")

    assert Company.objects.filter(name="Vela Pay").count() == 1


def test_seed_has_an_invited_member_without_a_password() -> None:
    call_command("seed_demo", "--force")

    member = CompanyMembership.objects.get(user__email="tuan.vo@velapay.vn")
    assert member.company.name == "Vela Pay"
    assert not member.user.has_usable_password()


def test_reset_removes_teammates_left_behind_by_e2e() -> None:
    from apps.accounts.models import User
    from apps.companies.seed import reset

    call_command("seed_demo", "--force")
    leftover = User.objects.create_user(
        email="e2e.123@velapay.vn", password=None, full_name="E2E Teammate", kind="company"
    )
    velapay = Company.objects.get(name="Vela Pay")
    CompanyMembership.objects.create(
        user=leftover,
        company=velapay,
        role=CompanyRole.TALENT_ACQUISITION,
        team=velapay.teams.first(),
    )

    reset()

    assert not User.objects.filter(email="e2e.123@velapay.vn").exists()


OLD_CATCH_ALL = "Team chung"  # what earlier seed runs created
GROWTH_ACTIVE = "Growth Marketing Manager"
GROWTH_DRAFT = "Merchant Acquisition Lead"
GROWTH_SUBMITTED = "Lifecycle Marketing Specialist"


def test_seed_demo_builds_two_teams() -> None:
    call_command("seed_demo", "--force")

    company = Company.objects.get(name="Vela Pay")
    assert set(company.teams.values_list("name", flat=True)) == {"Payments", "Growth"}
    seats = {
        m.user.email: (m.team.name if m.team else None, m.role, m.can_publish)
        for m in company.memberships.select_related("team", "user")
    }
    assert seats == {
        "nhung.le@velapay.vn": (None, CompanyRole.ADMIN, False),
        "maianh.tran@velapay.vn": ("Payments", CompanyRole.TALENT_ACQUISITION, True),
        "khoa.nguyen@velapay.vn": ("Payments", CompanyRole.HIRING_MANAGER, False),
        "huy.phan@velapay.vn": ("Growth", CompanyRole.HIRING_MANAGER, False),
        "thu.vo@velapay.vn": ("Growth", CompanyRole.TALENT_ACQUISITION, False),
        "tuan.vo@velapay.vn": ("Growth", CompanyRole.TALENT_ACQUISITION, False),
    }
    thu = User.objects.get(email="thu.vo@velapay.vn")
    assert thu.full_name == "Võ Hoài Thu"
    assert thu.check_password("demo123")
    payments = Job.objects.filter(company=company, team__name="Payments")
    assert payments.count() == 6


def test_seed_demo_has_draft_submitted_and_proposal() -> None:
    call_command("seed_demo", "--force")

    growth = Job.objects.filter(team__name="Growth")
    assert {j.title: j.status for j in growth} == {
        GROWTH_ACTIVE: JobStatus.ACTIVE,
        GROWTH_DRAFT: JobStatus.DRAFT,
        GROWTH_SUBMITTED: JobStatus.SUBMITTED,
    }
    assert {j.created_by_id for j in growth} == {User.objects.get(email="thu.vo@velapay.vn").pk}
    assert growth.get(title=GROWTH_ACTIVE).published_at is not None
    proposal = JobChangeRequest.objects.get()
    assert proposal.job.title == GROWTH_ACTIVE
    assert proposal.requested_by.email == "thu.vo@velapay.vn"
    assert proposal.kind == ChangeKind.EDIT
    assert proposal.status == ChangeStatus.PENDING
    assert proposal.changes


def test_seed_demo_twice_duplicates_nothing_and_leaves_no_team_chung() -> None:
    company_before = Company.objects.create(name="Vela Pay")
    Team.objects.create(company=company_before, name=OLD_CATCH_ALL)

    call_command("seed_demo", "--force")
    call_command("seed_demo", "--force")

    company = Company.objects.get(name="Vela Pay")
    assert sorted(company.teams.values_list("name", flat=True)) == ["Growth", "Payments"]
    assert Job.objects.filter(company=company).count() == 9
    assert JobChangeRequest.objects.count() == 1


def test_every_seeded_member_sees_the_jobs_their_seeded_objects_reference() -> None:
    call_command("seed_demo", "--force")

    for interview in Interview.objects.select_related("created_by", "application__job"):
        assert can_view_job(interview.created_by, interview.application.job)
    for feedback in Feedback.objects.select_related("author", "interview__application__job"):
        assert can_view_job(feedback.author, feedback.interview.application.job)
    for note in CandidateNote.objects.select_related("author", "application__job"):
        assert can_view_job(note.author, note.application.job)
    for approval in ApprovalRequest.objects.select_related(
        "approver", "requested_by", "application__job"
    ):
        assert can_view_job(approval.approver, approval.application.job)
        assert can_view_job(approval.requested_by, approval.application.job)
    khoa = User.objects.get(email="khoa.nguyen@velapay.vn")
    pending = ApprovalRequest.objects.get(status="pending")
    assert can_view_job(khoa, pending.application.job)


def test_growth_hiring_manager_cannot_see_payments_jobs() -> None:
    call_command("seed_demo", "--force")

    huy = User.objects.get(email="huy.phan@velapay.vn")
    for job in Job.objects.filter(team__name="Payments"):
        assert not can_view_job(huy, job)
    assert can_view_job(huy, Job.objects.get(title=GROWTH_ACTIVE))
