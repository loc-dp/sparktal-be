"""A job's Q&A thread (spec §5.3): top-level comments with one level of replies.

Anyone on the company side who can see the job reads and writes; so does every headhunter
assigned to it. A headhunter Sparktal took off the job but who has a CV on it still reads,
and writes nothing. A closed or deleted job is read-only for everyone.

Removing a comment (its author deletes it, or a job editor hides it) leaves the row and its
replies' rows as they are: `visible_comments` only walks visible parents, so the replies go out
of view with it.
"""

from uuid import UUID

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Q, QuerySet
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User, UserKind
from apps.jobs.access import can_change_job, can_view_job, is_assigned, jobs_open_to
from apps.jobs.models import Job, JobComment, JobStatus
from apps.jobs.notifications import notify_comment_posted
from apps.recruiters.access import approved_recruiter

COMMENT_MAX_LENGTH = 2000

JOB_READ_ONLY = "This job no longer takes comments."
NOT_ASSIGNED = "You are no longer assigned to this job."
CANNOT_REPLY = "This comment can no longer be replied to."
NOT_YOUR_COMMENT = "You can only delete your own comments."
CANNOT_HIDE = "Only someone who can edit this job can hide a comment."

VISIBLE = Q(hidden_at__isnull=True, deleted_at__isnull=True)


class CommentRejected(Exception):
    """The comment cannot be written; the message says why, in the user's words."""


def _is_recruiter(user: User) -> bool:
    return user.kind == UserKind.RECRUITER


def is_live(job: Job) -> bool:
    """A job still takes comments, and lets one be taken down; closed or deleted is read-only."""
    return job.status == JobStatus.ACTIVE and job.deleted_at is None


def can_read_comments(user: User | None, job: Job) -> bool:
    """A company user who can see the job, or an approved headhunter the job is open to."""
    if user is None:
        return False
    if _is_recruiter(user):
        recruiter = approved_recruiter(user)
        return recruiter is not None and jobs_open_to(recruiter).filter(pk=job.pk).exists()
    return can_view_job(user, job)


def can_write_comments(user: User | None, job: Job) -> bool:
    """A reader, on a live job; a headhunter must still hold their assignment."""
    if user is None or not is_live(job) or not can_read_comments(user, job):
        return False
    return not _is_recruiter(user) or is_assigned(approved_recruiter(user), job)


def can_delete_own(user: User, job: Job) -> bool:
    """`user` may take their own comments on `job` down: the job is live, and a headhunter still
    holds their assignment."""
    return is_live(job) and (not _is_recruiter(user) or is_assigned(approved_recruiter(user), job))


def can_hide_comments(user: User, job: Job) -> bool:
    """`user` may hide any comment on `job`: they can edit it and it is live."""
    return is_live(job) and can_change_job(user, job)


def author_name(comment: JobComment, viewer: User, company_name: str) -> str | None:
    """How `viewer` may name the author of `comment` (spec §5.3), the one rule for the API and
    the emails.

    A headhunter's nickname only to the company side and to the headhunter themselves, never
    to another headhunter (`None`). A company person is their name, else their email for a
    colleague and `company_name` for a headhunter. `None` too once the account is gone.
    """
    author = comment.author
    if author is None:
        return None
    if comment.from_recruiter:
        if viewer.kind != UserKind.RECRUITER or author.pk == viewer.pk:
            profile = getattr(author, "recruiter", None)
            return str(profile.nickname) if profile is not None else None
        return None
    if viewer.kind == UserKind.RECRUITER:
        return str(author.full_name or company_name)
    return str(author.full_name or author.email)


def visible_comments(job: Job) -> QuerySet[JobComment]:
    """Top-level comments still shown, newest first; each carries `visible_replies`, the
    replies still shown, oldest first. Authors (and a headhunter's profile) come joined."""
    authors = ("author", "author__recruiter")
    replies = (
        JobComment.objects.filter(VISIBLE).select_related(*authors).order_by("created_at", "id")
    )
    return (
        JobComment.objects.filter(VISIBLE, job=job, parent__isnull=True)
        .select_related(*authors)
        .prefetch_related(Prefetch("replies", queryset=replies, to_attr="visible_replies"))
        .order_by("-created_at", "-id")
    )


def add_comment(
    job: Job, author: User, body: str, *, parent: JobComment | None = None
) -> JobComment:
    """Write `body` on `job`, as a reply to `parent` when given.

    The caller has checked that `author` can read the thread (a 404 otherwise) and has
    validated `body`.
    """
    if not is_live(job):
        raise CommentRejected(JOB_READ_ONLY)
    from_recruiter = _is_recruiter(author)
    if from_recruiter and not is_assigned(approved_recruiter(author), job):
        raise CommentRejected(NOT_ASSIGNED)
    if parent is not None and (
        parent.parent_id is not None
        or parent.job_id != job.pk
        or parent.hidden_at is not None
        or parent.deleted_at is not None
    ):
        raise CommentRejected(CANNOT_REPLY)
    comment = JobComment.objects.create(
        job=job, author=author, from_recruiter=from_recruiter, parent=parent, body=body
    )
    transaction.on_commit(lambda: notify_comment_posted(comment))
    return comment


def delete_comment(comment: JobComment, user: User) -> None:
    """The author takes their own comment down; a headhunter only while still on the job, and
    nobody on a job that is closed or deleted. A comment already deleted stays as it was."""
    if comment.author_id != user.pk:
        raise PermissionDenied(NOT_YOUR_COMMENT)
    if not can_delete_own(user, comment.job):
        raise PermissionDenied(JOB_READ_ONLY if not is_live(comment.job) else NOT_ASSIGNED)
    if comment.deleted_at is not None:
        return
    comment.deleted_at = timezone.now()
    comment.save(update_fields=["deleted_at"])


def hide_comment(comment: JobComment, user: User) -> None:
    """Whoever may edit the job takes any comment on it down, unless the job is closed or
    deleted. A comment already hidden stays as it was (who hid it, and when)."""
    if not can_hide_comments(user, comment.job):
        raise PermissionDenied(JOB_READ_ONLY if can_change_job(user, comment.job) else CANNOT_HIDE)
    if comment.hidden_at is not None:
        return
    comment.hidden_at = timezone.now()
    comment.hidden_by = user
    comment.save(update_fields=["hidden_at", "hidden_by"])


def unanswered_counts(jobs: QuerySet[Job]) -> dict[UUID, int]:
    """Per job, the headhunters' top-level comments still shown that nobody on the company
    side has answered with a reply still shown; only jobs with any are keys."""
    company_answer = JobComment.objects.filter(VISIBLE, parent=OuterRef("pk"), from_recruiter=False)
    rows = (
        JobComment.objects.filter(VISIBLE, job__in=jobs, parent__isnull=True, from_recruiter=True)
        .filter(~Exists(company_answer))
        .order_by()
        .values("job_id")
        .annotate(total=Count("pk"))
    )
    return {row["job_id"]: row["total"] for row in rows}
