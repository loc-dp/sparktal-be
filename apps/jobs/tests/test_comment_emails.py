"""The emails a job comment sends (spec §5.3): who hears of it, what they are told, and that a
headhunter never learns who another headhunter is."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import pytest
from django.conf import settings
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.companies.models import CompanyMembership, CompanyRole
from apps.companies.tests.factories import CompanyMembershipFactory
from apps.jobs.comments import add_comment
from apps.jobs.models import Job, JobComment
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobAssignmentFactory
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

Commit = Callable[..., Any]

NICKNAME_B = "Atlas Hunt"
EMAIL_B = "atlas@hunt.example"


@dataclass(frozen=True)
class World:
    team: TeamWorld
    job: Job  # active, team A, created by `ta_publish` (who may edit it)
    a: RecruiterProfile  # assigned, "Bluefin Search"
    b: RecruiterProfile  # assigned, "Atlas Hunt"
    removed: RecruiterProfile  # taken off, never submitted
    outsider: RecruiterProfile  # approved, not on the job
    quiet: User  # a hiring manager in team A with no full name


@pytest.fixture
def world(team_world: TeamWorld) -> World:
    job = team_world.job_a
    a = cast(RecruiterProfile, RecruiterProfileFactory(nickname="Bluefin Search"))
    b = cast(RecruiterProfile, RecruiterProfileFactory(nickname=NICKNAME_B, user__email=EMAIL_B))
    removed = cast(RecruiterProfile, RecruiterProfileFactory(nickname="Quiet Exit"))
    outsider = cast(RecruiterProfile, RecruiterProfileFactory(nickname="Fog Bank"))
    for profile in (a, b):
        JobAssignmentFactory(job=job, recruiter=profile)
    JobAssignmentFactory(job=job, recruiter=removed, left_at=timezone.now())
    JobAssignmentFactory(job=job, recruiter=team_world.recruiter, left_at=timezone.now())
    quiet = cast(
        CompanyMembership,
        CompanyMembershipFactory(
            company=team_world.company,
            role=CompanyRole.HIRING_MANAGER,
            team=team_world.team_a,
            user__email="quiet@orbit.vn",
            user__full_name="",
        ),
    ).user
    return World(
        team=team_world, job=job, a=a, b=b, removed=removed, outsider=outsider, quiet=quiet
    )


def say(
    commit: Commit, job: Job, author: User, body: str, parent: JobComment | None = None
) -> JobComment:
    """Write a comment and run what it queued for after the commit."""
    with commit(execute=True):
        return add_comment(job, author, body, parent=parent)


def html_of(message: Any) -> str:
    return str(message.alternatives[0][0])


def recipients() -> list[str]:
    return sorted(address for message in mail.outbox for address in message.to)


def test_recruiter_comment_mails_admins_and_editing_creator(
    world: World, django_capture_on_commit_callbacks: Commit
) -> None:
    team = world.team
    inactive_admin = cast(
        CompanyMembership,
        CompanyMembershipFactory(
            company=team.company, role=CompanyRole.ADMIN, user__email="gone@orbit.vn"
        ),
    ).user
    User.objects.filter(pk=inactive_admin.pk).update(is_active=False)

    say(django_capture_on_commit_callbacks, world.job, world.a.user, "Is it hybrid?")

    # The hiring manager, the draft-only TA, the other team's manager and the outsider hear nothing.
    assert recipients() == sorted([team.admin.email, team.ta_publish.email])
    message = mail.outbox[0]
    assert message.subject == "New question on Payments Backend Engineer"
    assert "Bluefin Search" in message.body
    assert "Is it hybrid?" in message.body
    assert f"{settings.FRONTEND_URL}/jobs/{world.job.pk}#questions" in message.body
    assert message.from_email == settings.DEFAULT_FROM_EMAIL


def test_creator_who_cannot_edit_the_job_is_not_mailed(
    world: World, django_capture_on_commit_callbacks: Commit
) -> None:
    Job.objects.filter(pk=world.job.pk).update(created_by=world.team.ta_draft)
    world.job.refresh_from_db()

    say(django_capture_on_commit_callbacks, world.job, world.a.user, "Is it hybrid?")

    assert recipients() == [world.team.admin.email]


def test_an_admin_who_created_the_job_is_mailed_once(
    world: World, django_capture_on_commit_callbacks: Commit
) -> None:
    Job.objects.filter(pk=world.job.pk).update(created_by=world.team.admin)
    world.job.refresh_from_db()

    say(django_capture_on_commit_callbacks, world.job, world.a.user, "Is it hybrid?")

    assert recipients() == [world.team.admin.email]


def test_company_comment_mails_assigned_recruiters_only(
    world: World, django_capture_on_commit_callbacks: Commit
) -> None:
    say(django_capture_on_commit_callbacks, world.job, world.team.admin, "Two days in the office.")

    # Removed headhunters (with a CV or not) and the outsider get nothing; nor does the company.
    assert recipients() == sorted([world.a.user.email, world.b.user.email])
    message = mail.outbox[0]
    assert message.subject == "Orbit Pay posted on Payments Backend Engineer"
    assert "Two days in the office." in message.body
    assert f"{settings.FRONTEND_URL}/r/jobs/{world.job.pk}#questions" in message.body
    assert "/details#questions" not in message.body
    assert world.team.admin.full_name in message.body


def test_company_person_without_a_name_is_the_company_to_a_headhunter(
    world: World, django_capture_on_commit_callbacks: Commit
) -> None:
    say(django_capture_on_commit_callbacks, world.job, world.quiet, "Two days in the office.")

    for message in mail.outbox:
        assert "quiet@orbit.vn" not in message.body
        assert "quiet@orbit.vn" not in html_of(message)
        assert message.body.startswith("SPARKTAL\n\nOrbit Pay posted on")


def test_reply_mails_the_parent_author_once(
    world: World, django_capture_on_commit_callbacks: Commit
) -> None:
    top = say(django_capture_on_commit_callbacks, world.job, world.a.user, "Is it hybrid?")
    mail.outbox.clear()

    say(django_capture_on_commit_callbacks, world.job, world.team.hm_a, "Two days.", top)

    assert recipients() == [world.a.user.email]
    message = mail.outbox[0]
    assert message.subject == "New reply on Payments Backend Engineer"
    assert "Is it hybrid?" in message.body
    assert "Two days." in message.body
    assert world.team.hm_a.full_name in message.body

    mail.outbox.clear()
    company_top = say(django_capture_on_commit_callbacks, world.job, world.team.hm_a, "Note.")
    mail.outbox.clear()
    say(django_capture_on_commit_callbacks, world.job, world.b.user, "Thanks.", company_top)
    assert recipients() == [world.team.hm_a.email]
    assert "Atlas Hunt" in mail.outbox[0].body  # the company side may know the nickname


def test_reply_to_own_comment_mails_nobody(
    world: World, django_capture_on_commit_callbacks: Commit
) -> None:
    top = say(django_capture_on_commit_callbacks, world.job, world.a.user, "Is it hybrid?")
    mail.outbox.clear()

    say(django_capture_on_commit_callbacks, world.job, world.a.user, "Also, the salary?", top)

    assert mail.outbox == []


def test_recruiter_mail_never_names_another_recruiter(
    world: World, django_capture_on_commit_callbacks: Commit
) -> None:
    top = say(django_capture_on_commit_callbacks, world.job, world.a.user, "Is it hybrid?")
    mail.outbox.clear()

    say(django_capture_on_commit_callbacks, world.job, world.b.user, "Same question here.", top)

    (message,) = mail.outbox
    assert message.to == [world.a.user.email]
    html = html_of(message)
    headers = f"{message.from_email} {message.reply_to} {message.extra_headers} {message.cc}"
    for part in (message.subject, message.body, html, headers):
        assert NICKNAME_B not in part
        assert EMAIL_B not in part
        assert "atlas" not in part.lower()
    assert "Another headhunter" in message.body
    assert "Another headhunter" in html
    assert message.from_email == settings.DEFAULT_FROM_EMAIL
    assert message.reply_to == []


def test_no_mail_to_parent_author_who_lost_access(
    world: World, django_capture_on_commit_callbacks: Commit
) -> None:
    team = world.team
    from_removed = JobComment.objects.create(
        job=world.job, author=world.removed.user, from_recruiter=True, body="Hybrid?"
    )
    moved = cast(
        CompanyMembership,
        CompanyMembershipFactory(
            company=team.company,
            role=CompanyRole.HIRING_MANAGER,
            team=team.team_a,
            user__email="mover@orbit.vn",
        ),
    )
    from_moved = JobComment.objects.create(
        job=world.job, author=moved.user, from_recruiter=False, body="Heads up."
    )
    CompanyMembership.objects.filter(pk=moved.pk).update(team=team.team_b)
    from_inactive = JobComment.objects.create(
        job=world.job, author=world.b.user, from_recruiter=True, body="Salary?"
    )
    User.objects.filter(pk=world.b.user.pk).update(is_active=False)

    for parent in (from_removed, from_moved, from_inactive):
        say(django_capture_on_commit_callbacks, world.job, team.admin, "Answer.", parent)

    assert mail.outbox == []


def test_removed_recruiter_with_a_cv_who_reads_still_gets_no_company_comment(
    world: World, django_capture_on_commit_callbacks: Commit
) -> None:
    say(django_capture_on_commit_callbacks, world.job, world.team.hm_a, "Note for everyone.")

    assert world.team.recruiter.user.email not in recipients()


def test_a_comment_hidden_or_deleted_before_the_mail_goes_sends_nothing(
    world: World, django_capture_on_commit_callbacks: Commit
) -> None:
    from apps.jobs.notifications import notify_comment_posted

    top = say(django_capture_on_commit_callbacks, world.job, world.a.user, "Is it hybrid?")
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        reply = add_comment(world.job, world.team.admin, "Two days.", parent=top)
    JobComment.objects.filter(pk=top.pk).update(hidden_at=timezone.now())
    for callback in callbacks:
        callback()
    assert mail.outbox == []  # its parent was taken down

    JobComment.objects.filter(pk=top.pk).update(hidden_at=None)
    JobComment.objects.filter(pk=reply.pk).update(deleted_at=timezone.now())
    notify_comment_posted(reply)
    assert mail.outbox == []  # the reply itself was taken down

    JobComment.objects.filter(pk=reply.pk).update(deleted_at=None)
    notify_comment_posted(reply)
    assert recipients() == [world.a.user.email]


def test_comment_text_is_escaped_in_html_and_plain_in_text(
    world: World, django_capture_on_commit_callbacks: Commit
) -> None:
    say(
        django_capture_on_commit_callbacks,
        world.job,
        world.a.user,
        "<script>alert(1)</script> R&D\nsecond line",
    )

    message = mail.outbox[0]
    html = html_of(message)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; R&amp;D" in html
    assert "<script>alert(1)</script> R&D\nsecond line" in message.body


def test_email_failure_does_not_block_comment(
    world: World,
    client_for: Callable[[User], APIClient],
    django_capture_on_commit_callbacks: Commit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: Any, **kwargs: Any) -> int:
        raise ConnectionError("smtp is down")

    monkeypatch.setattr("django.core.mail.message.EmailMultiAlternatives.send", boom)

    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(world.a.user).post(
            f"/api/v1/jobs/{world.job.pk}/comments", {"body": "Is it hybrid?"}, format="json"
        )

    assert response.status_code == 201
    assert JobComment.objects.filter(job=world.job, body="Is it hybrid?").count() == 1


def test_a_failure_building_the_mail_is_swallowed_too(
    world: World,
    django_capture_on_commit_callbacks: Commit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("template is broken")

    monkeypatch.setattr("apps.jobs.notifications.build_email", boom)

    say(django_capture_on_commit_callbacks, world.job, world.a.user, "Is it hybrid?")

    assert mail.outbox == []
    assert JobComment.objects.count() == 1
