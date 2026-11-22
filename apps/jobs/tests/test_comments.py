"""The job's Q&A thread as rules (spec §5.3): who reads, who writes, what shows, what counts."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

import pytest
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.jobs.comments import (
    CANNOT_HIDE,
    CANNOT_REPLY,
    JOB_READ_ONLY,
    NOT_ASSIGNED,
    NOT_YOUR_COMMENT,
    CommentRejected,
    add_comment,
    can_read_comments,
    can_write_comments,
    delete_comment,
    hide_comment,
    unanswered_counts,
    visible_comments,
)
from apps.jobs.models import Job, JobComment, JobStatus
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobAssignmentFactory, JobCommentFactory
from apps.recruiters.models import RecruiterProfile, RecruiterStatus
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db


@dataclass(frozen=True)
class World:
    team: TeamWorld
    job: Job  # active, team A, created by `team.ta_publish`
    assigned: User  # holds an assignment on `job`
    other_assigned: User  # a second headhunter on `job`
    removed: User  # taken off `job`, with a CV on it (`team.recruiter`)
    stranger: User  # an approved headhunter with nothing to do with `job`
    removed_no_cv: User  # taken off `job` without ever having submitted a CV to it


@pytest.fixture
def world(team_world: TeamWorld) -> World:
    job = team_world.job_a
    assigned = cast(RecruiterProfile, RecruiterProfileFactory(nickname="Bluefin Search"))
    other = cast(RecruiterProfile, RecruiterProfileFactory(nickname="Atlas Hunt"))
    for recruiter in (assigned, other):
        JobAssignmentFactory(job=job, recruiter=recruiter)
    JobAssignmentFactory(job=job, recruiter=team_world.recruiter, left_at=timezone.now())
    stranger = cast(RecruiterProfile, RecruiterProfileFactory(nickname="Fog Bank"))
    quiet_exit = cast(RecruiterProfile, RecruiterProfileFactory(nickname="Quiet Exit"))
    JobAssignmentFactory(job=job, recruiter=quiet_exit, left_at=timezone.now())
    return World(
        team=team_world,
        job=job,
        assigned=assigned.user,
        other_assigned=other.user,
        removed=team_world.recruiter.user,
        stranger=stranger.user,
        removed_no_cv=quiet_exit.user,
    )


def _ids(comments: QuerySet[JobComment] | list[JobComment]) -> list[Any]:
    return [comment.pk for comment in comments]


def _back_date(comment: JobComment, hours: int) -> None:
    JobComment.objects.filter(pk=comment.pk).update(
        created_at=timezone.now() - timedelta(hours=hours)
    )


# --- who reads, who writes


def test_company_viewer_and_assigned_recruiter_can_write(world: World) -> None:
    team = world.team
    for user in (team.admin, team.ta_publish, team.hm_a, world.assigned):
        assert can_read_comments(user, world.job), user
        assert can_write_comments(user, world.job), user


def test_who_cannot_read(world: World) -> None:
    team = world.team
    pending = cast(RecruiterProfile, RecruiterProfileFactory(status=RecruiterStatus.PENDING))
    JobAssignmentFactory(job=world.job, recruiter=pending)

    staff = cast(User, UserFactory(is_staff=True, is_superuser=True))

    for user in (None, team.hm_b, world.stranger, pending.user, staff, world.removed_no_cv):
        assert not can_read_comments(user, world.job), user
        assert not can_write_comments(user, world.job), user


def test_removed_recruiter_with_a_cv_reads_but_cannot_write(world: World) -> None:
    assert can_read_comments(world.removed, world.job)
    assert not can_write_comments(world.removed, world.job)

    with pytest.raises(CommentRejected, match=NOT_ASSIGNED):
        add_comment(world.job, world.removed, "Still hiring?")
    assert not JobComment.objects.exists()


def test_recruiter_never_assigned_cannot_write(world: World) -> None:
    with pytest.raises(CommentRejected, match=NOT_ASSIGNED):
        add_comment(world.job, world.stranger, "Can I join?")
    assert not JobComment.objects.exists()


def test_closed_job_is_read_only_for_everyone(world: World) -> None:
    world.job.status = JobStatus.CLOSED
    world.job.save(update_fields=["status"])

    for user in (world.team.admin, world.team.hm_a, world.assigned):
        assert can_read_comments(user, world.job)
        assert not can_write_comments(user, world.job)
        with pytest.raises(CommentRejected, match=JOB_READ_ONLY):
            add_comment(world.job, user, "Any news?")
    assert not JobComment.objects.exists()


def test_deleted_job_takes_no_comments(world: World) -> None:
    world.job.deleted_at = timezone.now()
    world.job.save(update_fields=["deleted_at"])

    assert not can_write_comments(world.assigned, world.job)
    with pytest.raises(CommentRejected, match=JOB_READ_ONLY):
        add_comment(world.job, world.assigned, "Hello?")
    assert not JobComment.objects.exists()


# --- replies


def test_reply_to_reply_is_rejected(world: World) -> None:
    top = add_comment(world.job, world.assigned, "Is it hybrid?")
    reply = add_comment(world.job, world.team.admin, "Two days a week.", parent=top)

    with pytest.raises(CommentRejected, match=CANNOT_REPLY):
        add_comment(world.job, world.assigned, "Which days?", parent=reply)
    assert JobComment.objects.count() == 2


def test_reply_to_removed_comment_is_rejected(world: World) -> None:
    deleted = add_comment(world.job, world.assigned, "Is it hybrid?")
    delete_comment(deleted, world.assigned)
    with pytest.raises(CommentRejected, match=CANNOT_REPLY):
        add_comment(world.job, world.team.admin, "Yes.", parent=deleted)

    hidden = add_comment(world.job, world.assigned, "Is it remote?")
    hide_comment(hidden, world.team.admin)
    with pytest.raises(CommentRejected, match=CANNOT_REPLY):
        add_comment(world.job, world.team.admin, "No.", parent=hidden)

    assert JobComment.objects.count() == 2


def test_reply_across_jobs_is_rejected(world: World) -> None:
    elsewhere = cast(JobComment, JobCommentFactory(job=world.team.job_b, author=world.team.admin))

    with pytest.raises(CommentRejected, match=CANNOT_REPLY):
        add_comment(world.job, world.team.admin, "Wrong thread.", parent=elsewhere)
    assert JobComment.objects.count() == 1


# --- what shows


def test_thread_order(world: World) -> None:
    older = add_comment(world.job, world.assigned, "First question")
    newer = add_comment(world.job, world.other_assigned, "Second question")
    _back_date(older, 5)
    _back_date(newer, 4)
    late = add_comment(world.job, world.team.admin, "Second answer", parent=older)
    early = add_comment(world.job, world.assigned, "First answer", parent=older)
    _back_date(late, 1)
    _back_date(early, 2)

    thread = list(visible_comments(world.job))

    assert _ids(thread) == [newer.pk, older.pk]
    assert _ids(thread[1].visible_replies) == [early.pk, late.pk]
    assert thread[0].visible_replies == []


def test_visible_comments_loads_authors_in_a_fixed_number_of_queries(
    world: World, django_assert_num_queries: Any
) -> None:
    for user in (world.assigned, world.other_assigned, world.team.admin):
        top = add_comment(world.job, user, "A question")
        add_comment(world.job, world.team.hm_a, "An answer", parent=top)
        add_comment(world.job, world.assigned, "Thanks", parent=top)

    with django_assert_num_queries(2):
        for comment in visible_comments(world.job):
            names = [comment.author and comment.author.email]
            names += [getattr(comment.author, "recruiter", None)]
            for reply in comment.visible_replies:
                names += [reply.author and reply.author.email]
                names += [getattr(reply.author, "recruiter", None)]


def test_removed_parent_hides_its_replies(world: World) -> None:
    deleted = add_comment(world.job, world.assigned, "Is it hybrid?")
    deleted_reply = add_comment(world.job, world.team.admin, "Yes.", parent=deleted)
    hidden = add_comment(world.job, world.other_assigned, "Is it remote?")
    hidden_reply = add_comment(world.job, world.team.admin, "No.", parent=hidden)
    kept = add_comment(world.job, world.assigned, "What is the budget?")
    removed_reply = add_comment(world.job, world.team.admin, "Ignore this.", parent=kept)

    delete_comment(deleted, world.assigned)
    hide_comment(hidden, world.team.ta_publish)
    delete_comment(removed_reply, world.team.admin)

    thread = list(visible_comments(world.job))
    assert _ids(thread) == [kept.pk]
    assert thread[0].visible_replies == []
    # The replies themselves are left as they were.
    for reply in (deleted_reply, hidden_reply):
        reply.refresh_from_db()
        assert (reply.hidden_at, reply.deleted_at) == (None, None)


# --- delete and hide


def test_only_the_author_deletes(world: World) -> None:
    comment = add_comment(world.job, world.assigned, "Is it hybrid?")

    for user in (world.other_assigned, world.team.admin, world.team.hm_a):
        with pytest.raises(PermissionDenied, match=NOT_YOUR_COMMENT):
            delete_comment(comment, user)
    comment.refresh_from_db()
    assert comment.deleted_at is None

    delete_comment(comment, world.assigned)

    comment.refresh_from_db()
    assert comment.deleted_at is not None


def test_company_author_deletes_own_comment(world: World) -> None:
    comment = add_comment(world.job, world.team.hm_a, "We are also open to Da Nang.")

    delete_comment(comment, world.team.hm_a)

    comment.refresh_from_db()
    assert comment.deleted_at is not None


def test_removed_recruiter_cannot_delete_own_comment(world: World) -> None:
    comment = add_comment(world.job, world.assigned, "Is it hybrid?")
    world.assigned.recruiter.assignments.update(left_at=timezone.now())

    with pytest.raises(PermissionDenied, match=NOT_ASSIGNED):
        delete_comment(comment, world.assigned)
    comment.refresh_from_db()
    assert comment.deleted_at is None


def test_only_a_job_editor_hides(world: World) -> None:
    comment = add_comment(world.job, world.assigned, "Is it hybrid?")

    for user in (world.team.hm_a, world.team.ta_draft, world.assigned, world.team.hm_b):
        with pytest.raises(PermissionDenied, match=CANNOT_HIDE):
            hide_comment(comment, user)
    comment.refresh_from_db()
    assert comment.hidden_at is None

    hide_comment(comment, world.team.admin)

    comment.refresh_from_db()
    assert comment.hidden_at is not None
    assert comment.hidden_by == world.team.admin


def test_closed_job_lets_nobody_take_a_comment_down(world: World) -> None:
    mine = add_comment(world.job, world.assigned, "Is it hybrid?")
    world.job.status = JobStatus.CLOSED
    world.job.save(update_fields=["status"])
    mine = JobComment.objects.select_related("job").get(pk=mine.pk)

    with pytest.raises(PermissionDenied, match=JOB_READ_ONLY):
        delete_comment(mine, world.assigned)
    with pytest.raises(PermissionDenied, match=JOB_READ_ONLY):
        hide_comment(mine, world.team.admin)
    # Whoever may not still hears why not, not that the job is closed.
    with pytest.raises(PermissionDenied, match=NOT_YOUR_COMMENT):
        delete_comment(mine, world.team.admin)
    with pytest.raises(PermissionDenied, match=CANNOT_HIDE):
        hide_comment(mine, world.assigned)

    mine.refresh_from_db()
    assert (mine.hidden_at, mine.hidden_by, mine.deleted_at) == (None, None, None)


def test_deleted_job_lets_its_headhunter_take_nothing_down(world: World) -> None:
    mine = add_comment(world.job, world.assigned, "Is it hybrid?")
    world.job.deleted_at = timezone.now()
    world.job.save(update_fields=["deleted_at"])
    mine = JobComment.objects.select_related("job").get(pk=mine.pk)

    with pytest.raises(PermissionDenied, match=JOB_READ_ONLY):
        delete_comment(mine, world.assigned)
    # The company side has lost sight of a deleted job, so it cannot edit it either.
    with pytest.raises(PermissionDenied, match=CANNOT_HIDE):
        hide_comment(mine, world.team.admin)

    mine.refresh_from_db()
    assert (mine.hidden_at, mine.deleted_at) == (None, None)


def test_hiding_twice_keeps_the_first_hide(world: World) -> None:
    comment = add_comment(world.job, world.assigned, "Is it hybrid?")
    hide_comment(comment, world.team.admin)
    first = JobComment.objects.get(pk=comment.pk)

    hide_comment(first, world.team.ta_publish)

    again = JobComment.objects.get(pk=comment.pk)
    assert (again.hidden_at, again.hidden_by) == (first.hidden_at, first.hidden_by)
    assert again.hidden_by == world.team.admin


def test_deleting_twice_keeps_the_first_delete(world: World) -> None:
    comment = add_comment(world.job, world.assigned, "Is it hybrid?")
    delete_comment(comment, world.assigned)
    first = JobComment.objects.get(pk=comment.pk)

    delete_comment(first, world.assigned)

    assert JobComment.objects.get(pk=comment.pk).deleted_at == first.deleted_at


# --- what counts


def test_unanswered_counts(world: World) -> None:
    team = world.team
    jobs = Job.objects.filter(pk__in=[world.job.pk, team.job_b.pk])
    assert unanswered_counts(jobs) == {}

    waiting = add_comment(world.job, world.assigned, "No reply yet")
    assert unanswered_counts(jobs) == {world.job.pk: 1}

    add_comment(world.job, world.other_assigned, "Same question here", parent=waiting)
    assert unanswered_counts(jobs) == {world.job.pk: 1}

    answered = add_comment(world.job, world.other_assigned, "Answered")
    add_comment(world.job, team.hm_a, "Here is the answer", parent=answered)
    assert unanswered_counts(jobs) == {world.job.pk: 1}

    hidden = add_comment(world.job, world.assigned, "Hidden")
    hide_comment(hidden, team.admin)
    deleted = add_comment(world.job, world.assigned, "Deleted")
    delete_comment(deleted, world.assigned)
    add_comment(world.job, team.admin, "A note from the company")
    assert unanswered_counts(jobs) == {world.job.pk: 1}

    # An answer that was removed no longer answers.
    removed = add_comment(world.job, world.assigned, "Answer removed")
    answer = add_comment(world.job, team.hm_a, "Wrong answer", parent=removed)
    delete_comment(answer, team.hm_a)
    assert unanswered_counts(jobs) == {world.job.pk: 2}

    # Another job's comments count for that job only.
    JobCommentFactory(job=team.job_b)
    assert unanswered_counts(jobs) == {world.job.pk: 2, team.job_b.pk: 1}
    assert unanswered_counts(Job.objects.filter(pk=team.job_b.pk)) == {team.job_b.pk: 1}


def test_from_recruiter_follows_the_author(world: World) -> None:
    by_recruiter = add_comment(world.job, world.assigned, "Is it hybrid?")
    by_company = add_comment(world.job, world.team.hm_a, "Two days a week.", parent=by_recruiter)

    assert (by_recruiter.from_recruiter, by_recruiter.author) == (True, world.assigned)
    assert (by_company.from_recruiter, by_company.author) == (False, world.team.hm_a)
    assert by_company.parent == by_recruiter


def test_a_new_comment_is_announced_after_commit(
    world: World, django_capture_on_commit_callbacks: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    told: list[JobComment] = []
    monkeypatch.setattr("apps.jobs.comments.notify_comment_posted", told.append)

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        comment = add_comment(world.job, world.assigned, "Is it hybrid?")
    assert told == []

    for callback in callbacks:
        callback()
    assert told == [comment]
