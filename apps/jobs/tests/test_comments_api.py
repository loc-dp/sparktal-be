"""The job's Q&A thread over HTTP (spec §5.3): who reads, who writes, who is named, who deletes.

The comment rules themselves are in `test_comments.py`; this file checks the doors: a 404 for
anyone who may not read the thread, the sentences a refusal answers with, and above all that a
headhunter never learns who another headhunter is.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.candidates.tests.factories import ApplicationFactory
from apps.companies.models import CompanyMembership, CompanyRole
from apps.companies.tests.factories import CompanyMembershipFactory
from apps.jobs.comments import (
    CANNOT_HIDE,
    CANNOT_REPLY,
    JOB_READ_ONLY,
    NOT_ASSIGNED,
    NOT_YOUR_COMMENT,
    add_comment,
)
from apps.jobs.models import Job, JobComment, JobStatus
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobAssignmentFactory, JobCommentFactory
from apps.recruiters.models import RecruiterProfile, RecruiterStatus
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

ClientFor = Callable[[User], APIClient]


@dataclass(frozen=True)
class World:
    team: TeamWorld
    job: Job  # active, team A
    a: RecruiterProfile  # assigned, "Bluefin Search"
    b: RecruiterProfile  # assigned, "Atlas Hunt"
    c: RecruiterProfile  # approved, not on the job at all
    removed_with_cv: RecruiterProfile  # taken off, still has a CV on it
    removed: RecruiterProfile  # taken off, never submitted
    quiet: User  # a hiring manager in team A with no full name


@pytest.fixture
def world(team_world: TeamWorld) -> World:
    job = team_world.job_a
    a = cast(RecruiterProfile, RecruiterProfileFactory(nickname="Bluefin Search"))
    b = cast(
        RecruiterProfile,
        RecruiterProfileFactory(nickname="Atlas Hunt", user__email="atlas@hunt.example"),
    )
    c = cast(RecruiterProfile, RecruiterProfileFactory(nickname="Fog Bank"))
    removed = cast(RecruiterProfile, RecruiterProfileFactory(nickname="Quiet Exit"))
    for profile in (a, b):
        JobAssignmentFactory(job=job, recruiter=profile)
    JobAssignmentFactory(job=job, recruiter=team_world.recruiter, left_at=timezone.now())
    JobAssignmentFactory(job=job, recruiter=removed, left_at=timezone.now())
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
        team=team_world,
        job=job,
        a=a,
        b=b,
        c=c,
        removed_with_cv=team_world.recruiter,
        removed=removed,
        quiet=quiet,
    )


def thread_url(job: Job) -> str:
    return f"/api/v1/jobs/{job.pk}/comments"


def comment_url(comment: JobComment) -> str:
    return f"/api/v1/job-comments/{comment.pk}"


def hide_url(comment: JobComment) -> str:
    return f"{comment_url(comment)}/hide"


def post(client: APIClient, job: Job, body: str = "Is it hybrid?", **extra: Any) -> Response:
    return cast(Response, client.post(thread_url(job), {"body": body, **extra}, format="json"))


def thread(client: APIClient, job: Job) -> dict[str, Any]:
    response = client.get(thread_url(job))
    assert response.status_code == 200
    return cast(dict[str, Any], response.json())


def say(job: Job, author: User, body: str, parent: JobComment | None = None) -> JobComment:
    return add_comment(job, author, body, parent=parent)


# --- reading and writing


def test_company_viewers_and_assigned_recruiters_list_and_post(
    world: World, client_for: ClientFor
) -> None:
    team = world.team
    for user in (team.admin, team.ta_publish, team.hm_a, world.a.user, world.b.user):
        client = client_for(user)
        assert client.get(thread_url(world.job)).status_code == 200, user
        response = post(client, world.job, f"Hello from {user.pk}")
        assert response.status_code == 201, user
        assert response.json()["mine"] is True

    assert JobComment.objects.filter(job=world.job).count() == 5


def test_a_posted_comment_comes_back_as_the_viewer_sees_it(
    world: World, client_for: ClientFor
) -> None:
    body = post(client_for(world.a.user), world.job, "  Is it hybrid?  ").json()

    assert body["body"] == "Is it hybrid?"
    assert body["author_kind"] == "recruiter"
    assert body["author_name"] == "Bluefin Search"
    assert (body["mine"], body["can_delete"], body["replies"]) == (True, True, [])
    assert set(body) == {
        "id",
        "body",
        "created_at",
        "author_kind",
        "author_name",
        "mine",
        "can_delete",
        "replies",
    }


def test_thread_lists_newest_first_with_replies_oldest_first(
    world: World, client_for: ClientFor
) -> None:
    older = say(world.job, world.a.user, "First question")
    newer = say(world.job, world.b.user, "Second question")
    JobComment.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(hours=5))
    JobComment.objects.filter(pk=newer.pk).update(created_at=timezone.now() - timedelta(hours=4))
    late = say(world.job, world.team.admin, "Late answer", older)
    early = say(world.job, world.team.hm_a, "Early answer", older)
    JobComment.objects.filter(pk=late.pk).update(created_at=timezone.now() - timedelta(hours=1))
    JobComment.objects.filter(pk=early.pk).update(created_at=timezone.now() - timedelta(hours=2))

    comments = thread(client_for(world.team.admin), world.job)["comments"]

    assert [row["id"] for row in comments] == [str(newer.pk), str(older.pk)]
    assert [row["id"] for row in comments[1]["replies"]] == [str(early.pk), str(late.pk)]
    assert comments[0]["replies"] == []
    assert comments[1]["replies"][0]["replies"] == []


def test_every_comment_carries_replies_a_reply_has_none(
    world: World, client_for: ClientFor
) -> None:
    top = say(world.job, world.a.user, "Is it hybrid?")
    say(world.job, world.team.hm_a, "Two days.", top)

    response = post(client_for(world.team.admin), world.job, "Three days.", parent=str(top.pk))

    assert response.status_code == 201
    assert response.json()["replies"] == []
    assert JobComment.objects.get(pk=response.json()["id"]).parent == top
    row = thread(client_for(world.a.user), world.job)["comments"][0]
    assert [reply["replies"] for reply in row["replies"]] == [[], []]


def test_body_bounds(world: World, client_for: ClientFor) -> None:
    client = client_for(world.team.admin)

    for body in ("", "   ", "\n\t "):
        assert post(client, world.job, body).status_code == 400, repr(body)
    assert post(client, world.job, "x" * 2001).status_code == 400
    assert client.post(thread_url(world.job), {}, format="json").status_code == 400
    assert post(client, world.job, "x" * 2000).status_code == 201
    assert JobComment.objects.count() == 1


@pytest.mark.parametrize("parent", ["unknown", "other_job", "reply", "hidden", "deleted"])
def test_a_reply_to_something_that_cannot_be_replied_to_is_refused(
    parent: str, world: World, client_for: ClientFor
) -> None:
    admin = world.team.admin
    top = say(world.job, world.a.user, "Is it hybrid?")
    target = {
        "unknown": "7d7a41c0-5c47-4d33-a4f2-d0a1d1b8b6c1",
        "other_job": str(JobCommentFactory(job=world.team.job_b, author=admin).pk),
        "reply": str(say(world.job, admin, "Two days.", top).pk),
        "hidden": str(say(world.job, world.b.user, "Is it remote?").pk),
        "deleted": str(say(world.job, world.b.user, "Which days?").pk),
    }
    JobComment.objects.filter(pk=target["hidden"]).update(hidden_at=timezone.now())
    JobComment.objects.filter(pk=target["deleted"]).update(deleted_at=timezone.now())
    before = JobComment.objects.count()

    response = post(client_for(admin), world.job, "An answer.", parent=target[parent])

    assert response.status_code == 400
    assert response.json() == {"detail": CANNOT_REPLY}
    assert JobComment.objects.count() == before


# --- who may not


def test_other_team_gets_404_everywhere(world: World, client_for: ClientFor) -> None:
    comment = say(world.job, world.a.user, "Is it hybrid?")
    client = client_for(world.team.hm_b)

    assert client.get(thread_url(world.job)).status_code == 404
    assert post(client, world.job).status_code == 404
    assert post(client, world.job, parent=str(comment.pk)).status_code == 404
    assert client.delete(comment_url(comment)).status_code == 404
    assert client.post(hide_url(comment), {}, format="json").status_code == 404
    comment.refresh_from_db()
    assert (comment.hidden_at, comment.deleted_at) == (None, None)
    assert JobComment.objects.count() == 1


@pytest.mark.parametrize("who", ["c", "pending", "rejected", "staff", "removed_company"])
def test_everyone_else_gets_404(who: str, world: World, client_for: ClientFor) -> None:
    comment = say(world.job, world.a.user, "Is it hybrid?")
    pending = cast(RecruiterProfile, RecruiterProfileFactory(status=RecruiterStatus.PENDING))
    rejected = cast(RecruiterProfile, RecruiterProfileFactory(status=RecruiterStatus.REJECTED))
    for profile in (pending, rejected):
        JobAssignmentFactory(job=world.job, recruiter=profile)
    users = {
        "c": world.c.user,
        "pending": pending.user,
        "rejected": rejected.user,
        "staff": cast(User, UserFactory(is_staff=True, is_superuser=True)),
        "removed_company": cast(User, UserFactory()),
    }
    client = client_for(users[who])

    assert client.get(thread_url(world.job)).status_code == 404
    assert post(client, world.job).status_code == 404
    assert client.delete(comment_url(comment)).status_code == 404
    assert client.post(hide_url(comment), {}, format="json").status_code == 404
    assert JobComment.objects.count() == 1


def test_an_anonymous_caller_is_turned_away(world: World) -> None:
    comment = say(world.job, world.a.user, "Is it hybrid?")
    client = APIClient()

    assert client.get(thread_url(world.job)).status_code in (401, 403)
    assert client.post(thread_url(world.job), {"body": "Hi"}, format="json").status_code in (
        401,
        403,
    )
    assert client.delete(comment_url(comment)).status_code in (401, 403)


def test_a_missing_job_or_comment_is_404(world: World, client_for: ClientFor) -> None:
    client = client_for(world.team.admin)
    nowhere = "7d7a41c0-5c47-4d33-a4f2-d0a1d1b8b6c1"

    assert client.get(f"/api/v1/jobs/{nowhere}/comments").status_code == 404
    assert client.delete(f"/api/v1/job-comments/{nowhere}").status_code == 404
    assert client.post(f"/api/v1/job-comments/{nowhere}/hide", {}, format="json").status_code == 404


def test_removed_recruiter_reads_only(world: World, client_for: ClientFor) -> None:
    mine = cast(JobComment, JobCommentFactory(job=world.job, author=world.removed_with_cv.user))
    client = client_for(world.removed_with_cv.user)

    body = thread(client, world.job)
    assert body["can_write"] is False
    assert [row["id"] for row in body["comments"]] == [str(mine.pk)]
    assert body["comments"][0]["can_delete"] is False
    response = post(client, world.job)
    assert (response.status_code, response.json()) == (400, {"detail": NOT_ASSIGNED})
    response = client.delete(comment_url(mine))
    assert (response.status_code, response.json()) == (403, {"detail": NOT_ASSIGNED})
    mine.refresh_from_db()
    assert mine.deleted_at is None


def test_removed_recruiter_without_cv_gets_404(world: World, client_for: ClientFor) -> None:
    client = client_for(world.removed.user)

    assert client.get(thread_url(world.job)).status_code == 404
    assert post(client, world.job).status_code == 404


def test_recruiter_whose_cv_is_on_a_job_they_never_held_reads(
    world: World, client_for: ClientFor
) -> None:
    """`jobs_open_to`: a CV is enough to read, an assignment is what writes."""
    ApplicationFactory(job=world.job, recruiter=world.c)
    client = client_for(world.c.user)

    assert thread(client, world.job)["can_write"] is False
    assert post(client, world.job).json() == {"detail": NOT_ASSIGNED}


# --- closed and deleted jobs


@pytest.fixture
def closed_job_with_comment(world: World) -> JobComment:
    comment = say(world.job, world.a.user, "Is it hybrid?")
    world.job.status = JobStatus.CLOSED
    world.job.save(update_fields=["status"])
    return comment


def test_closed_job_is_read_only(
    world: World, closed_job_with_comment: JobComment, client_for: ClientFor
) -> None:
    comment = closed_job_with_comment
    for user in (world.team.admin, world.team.hm_a, world.a.user):
        client = client_for(user)
        body = thread(client, world.job)
        assert (body["can_write"], body["can_hide"]) == (False, False), user
        assert [row["can_delete"] for row in body["comments"]] == [False], user
        response = post(client, world.job)
        assert (response.status_code, response.json()) == (400, {"detail": JOB_READ_ONLY}), user

    # Nobody takes a comment down on a closed job: not its author, not an editor.
    for user in (world.a.user, world.team.admin):
        client = client_for(user)
        response = client.delete(comment_url(comment))
        expected = JOB_READ_ONLY if user == world.a.user else NOT_YOUR_COMMENT
        assert (response.status_code, response.json()) == (403, {"detail": expected})
        response = client.post(hide_url(comment), {}, format="json")
        expected = CANNOT_HIDE if user == world.a.user else JOB_READ_ONLY
        assert (response.status_code, response.json()) == (403, {"detail": expected})
    comment.refresh_from_db()
    assert (comment.hidden_at, comment.deleted_at) == (None, None)
    assert JobComment.objects.count() == 1


def test_a_deleted_job_is_read_only_for_the_headhunter_who_can_still_open_it(
    world: World, client_for: ClientFor
) -> None:
    comment = say(world.job, world.a.user, "Is it hybrid?")
    world.job.deleted_at = timezone.now()
    world.job.save(update_fields=["deleted_at"])

    assert client_for(world.team.admin).get(thread_url(world.job)).status_code == 404
    client = client_for(world.a.user)
    body = thread(client, world.job)
    assert (body["can_write"], body["can_hide"]) == (False, False)
    assert body["comments"][0]["can_delete"] is False
    assert post(client, world.job).json() == {"detail": JOB_READ_ONLY}
    response = client.delete(comment_url(comment))
    assert (response.status_code, response.json()) == (403, {"detail": JOB_READ_ONLY})


# --- deleting and hiding


def test_author_deletes_own_comment_only(world: World, client_for: ClientFor) -> None:
    team = world.team
    comment = say(world.job, world.a.user, "Is it hybrid?")

    for user in (world.b.user, team.admin, team.hm_a):
        response = client_for(user).delete(comment_url(comment))
        assert (response.status_code, response.json()) == (403, {"detail": NOT_YOUR_COMMENT}), user
    comment.refresh_from_db()
    assert comment.deleted_at is None

    assert client_for(world.a.user).delete(comment_url(comment)).status_code == 204
    comment.refresh_from_db()
    assert comment.deleted_at is not None

    own = say(world.job, team.hm_a, "We are also open to Da Nang.")
    assert client_for(team.hm_a).delete(comment_url(own)).status_code == 204


def test_a_recruiter_taken_off_the_job_cannot_delete_their_comment(
    world: World, client_for: ClientFor
) -> None:
    comment = say(world.job, world.a.user, "Is it hybrid?")
    ApplicationFactory(job=world.job, recruiter=world.a)
    world.a.assignments.update(left_at=timezone.now())

    response = client_for(world.a.user).delete(comment_url(comment))

    assert (response.status_code, response.json()) == (403, {"detail": NOT_ASSIGNED})


def test_editor_hides_any_comment_viewer_cannot(world: World, client_for: ClientFor) -> None:
    team = world.team
    comment = say(world.job, world.a.user, "Is it hybrid?")

    for user in (team.hm_a, team.ta_draft, world.a.user, world.b.user):
        response = client_for(user).post(hide_url(comment), {}, format="json")
        assert (response.status_code, response.json()) == (403, {"detail": CANNOT_HIDE}), user
    comment.refresh_from_db()
    assert comment.hidden_at is None

    assert client_for(team.admin).post(hide_url(comment), {}, format="json").status_code == 204
    comment.refresh_from_db()
    assert comment.hidden_at is not None
    assert comment.hidden_by == team.admin

    other = say(world.job, team.hm_a, "A note.")
    assert client_for(team.ta_publish).post(hide_url(other), {}, format="json").status_code == 204


def test_removed_comment_is_404_afterwards(world: World, client_for: ClientFor) -> None:
    team = world.team
    hidden = say(world.job, world.a.user, "Hidden one")
    deleted = say(world.job, world.a.user, "Deleted one")
    reply_of_hidden = say(world.job, team.admin, "Answer to the hidden one", hidden)
    admin = client_for(team.admin)
    assert admin.post(hide_url(hidden), {}, format="json").status_code == 204
    assert client_for(world.a.user).delete(comment_url(deleted)).status_code == 204
    hidden_at = JobComment.objects.get(pk=hidden.pk).hidden_at

    for comment in (hidden, deleted):
        assert admin.delete(comment_url(comment)).status_code == 404
        assert admin.post(hide_url(comment), {}, format="json").status_code == 404
        assert client_for(world.a.user).delete(comment_url(comment)).status_code == 404
    # Only the removed comment's own row changes; its reply is left as it was.
    assert thread(admin, world.job)["comments"] == []
    assert JobComment.objects.get(pk=hidden.pk).hidden_at == hidden_at
    assert JobComment.objects.get(pk=reply_of_hidden.pk).hidden_at is None


def test_deleted_author_still_renders(world: World, client_for: ClientFor) -> None:
    by_recruiter = say(world.job, world.a.user, "Is it hybrid?")
    by_member = say(world.job, world.team.hm_a, "Two days a week.", by_recruiter)
    JobComment.objects.filter(pk__in=[by_recruiter.pk, by_member.pk]).update(author=None)

    for user in (world.team.admin, world.b.user):
        row = thread(client_for(user), world.job)["comments"][0]
        assert (row["author_kind"], row["author_name"]) == ("recruiter", None)
        assert (row["mine"], row["can_delete"]) == (False, False)
        reply = row["replies"][0]
        assert (reply["author_kind"], reply["author_name"]) == ("company", None)


@pytest.mark.parametrize("how", ["hidden", "deleted"])
def test_a_reply_under_a_removed_parent_is_404(
    how: str, world: World, client_for: ClientFor
) -> None:
    parent = say(world.job, world.a.user, "Is it hybrid?")
    own = say(world.job, world.team.admin, "Two days.", parent)
    JobComment.objects.filter(pk=parent.pk).update(**{f"{how}_at": timezone.now()})
    admin = client_for(world.team.admin)

    assert admin.delete(comment_url(own)).status_code == 404
    assert admin.post(hide_url(own), {}, format="json").status_code == 404
    own.refresh_from_db()
    assert (own.hidden_at, own.deleted_at) == (None, None)


# --- who is named


def test_company_sees_recruiter_nicknames(world: World, client_for: ClientFor) -> None:
    say(world.job, world.a.user, "From A")
    say(world.job, world.b.user, "From B")
    names = {
        row["body"]: row["author_name"]
        for row in thread(client_for(world.team.admin), world.job)["comments"]
    }

    assert names == {"From A": "Bluefin Search", "From B": "Atlas Hunt"}


def test_company_sees_a_colleague_by_name_else_by_email(
    world: World, client_for: ClientFor
) -> None:
    say(world.job, world.team.hm_a, "Named")
    say(world.job, world.quiet, "Unnamed")

    rows = thread(client_for(world.team.ta_publish), world.job)["comments"]

    assert {row["body"]: row["author_name"] for row in rows} == {
        "Named": world.team.hm_a.full_name,
        "Unnamed": "quiet@orbit.vn",
    }
    assert {row["body"]: row["author_kind"] for row in rows} == {
        "Named": "company",
        "Unnamed": "company",
    }


def test_recruiter_sees_company_name_not_email(world: World, client_for: ClientFor) -> None:
    say(world.job, world.quiet, "Unnamed")
    say(world.job, world.team.hm_a, "Named")

    rows = thread(client_for(world.a.user), world.job)["comments"]

    assert {row["body"]: row["author_name"] for row in rows} == {
        "Unnamed": "Orbit Pay",
        "Named": world.team.hm_a.full_name,
    }
    assert "quiet@orbit.vn" not in json.dumps(rows)


def test_recruiter_payload_never_names_other_recruiters(
    world: World, client_for: ClientFor
) -> None:
    top_b = say(world.job, world.b.user, "B asks something")
    say(world.job, world.team.admin, "The company answers", top_b)
    say(world.job, world.b.user, "B follows up", top_b)
    own = say(world.job, world.a.user, "A asks something")
    say(world.job, world.b.user, "B answers A's question", own)
    client = client_for(world.a.user)

    # Everything A can be shown: the thread, A's own post and reply, and the refusals.
    responses = [
        client.get(thread_url(world.job)),
        post(client, world.job, "A again"),
        post(client, world.job, "A replies to B", parent=str(top_b.pk)),
        post(client, world.job, "x" * 2001),
        post(client, world.job, "A replies to a missing comment", parent="not-a-uuid"),
        post(client, world.job, "A replies to a reply", parent=str(own.pk)),
        client.delete(comment_url(top_b)),
        client.post(hide_url(top_b), {}, format="json"),
    ]
    text = " ".join(response.content.decode() for response in responses)

    for secret in (
        world.b.nickname,
        str(world.b.pk),
        str(world.b.user.pk),
        world.b.user.pk.hex,
        world.b.pk.hex,
        world.b.user.email,
        world.b.user.full_name,
    ):
        assert secret, "a marker to look for"
        assert secret not in text, secret
    rows = responses[0].json()["comments"]
    by_body = {row["body"]: row for row in rows}
    b_rows = [by_body["B asks something"], *by_body["B asks something"]["replies"]]
    b_rows.append(by_body["A asks something"]["replies"][0])
    assert [row["body"] for row in b_rows] == [
        "B asks something",
        "The company answers",
        "B follows up",
        "B answers A's question",
    ]
    for row in (b_rows[0], b_rows[2], b_rows[3]):
        assert (row["author_kind"], row["author_name"], row["mine"]) == ("recruiter", None, False)
    own_row = by_body["A asks something"]
    assert (own_row["author_name"], own_row["mine"]) == ("Bluefin Search", True)


def test_no_author_id_is_ever_in_a_comment(world: World, client_for: ClientFor) -> None:
    top = say(world.job, world.a.user, "Is it hybrid?")
    say(world.job, world.team.admin, "Two days.", top)

    for user in (world.team.admin, world.a.user):
        rows = thread(client_for(user), world.job)["comments"]
        keys = set(rows[0]) | set(rows[0]["replies"][0])
        assert {key for key in keys if "author" in key or "user" in key} == {
            "author_kind",
            "author_name",
        }


# --- flags


def test_can_delete_and_can_hide_flags(world: World, client_for: ClientFor) -> None:
    team = world.team
    by_a = say(world.job, world.a.user, "From A")
    by_hm = say(world.job, team.hm_a, "From the manager", by_a)
    say(world.job, world.b.user, "From B")

    def flags(user: User) -> tuple[bool, bool, set[str]]:
        body = thread(client_for(user), world.job)
        rows = [*body["comments"], *body["comments"][1]["replies"]]
        mine = {row["body"] for row in rows if row["mine"]}
        deletable = {row["body"] for row in rows if row["can_delete"]}
        assert deletable == mine
        return body["can_write"], body["can_hide"], deletable

    assert flags(world.a.user) == (True, False, {"From A"})
    assert flags(world.b.user) == (True, False, {"From B"})
    assert flags(team.hm_a) == (True, False, {"From the manager"})
    assert flags(team.admin) == (True, True, set())
    assert flags(team.ta_publish) == (True, True, set())
    assert by_hm.parent == by_a


def test_a_recruiter_taken_off_the_job_has_nothing_to_delete(
    world: World, client_for: ClientFor
) -> None:
    say(world.job, world.a.user, "From A")
    ApplicationFactory(job=world.job, recruiter=world.a)
    world.a.assignments.update(left_at=timezone.now())

    body = thread(client_for(world.a.user), world.job)

    assert body["can_write"] is False
    assert [(row["mine"], row["can_delete"]) for row in body["comments"]] == [(True, False)]


# --- limits and plumbing


def test_post_is_throttled_get_is_not(
    world: World, client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache.clear()
    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"job_comment": "2/min"})
    client = client_for(world.team.admin)

    codes = [post(client, world.job, f"Comment {n}").status_code for n in range(3)]

    assert codes == [201, 201, 429]
    for _ in range(4):
        assert client.get(thread_url(world.job)).status_code == 200
    assert JobComment.objects.count() == 2
    cache.clear()


def test_delete_and_hide_are_not_throttled(
    world: World, client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache.clear()
    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"job_comment": "1/min"})
    comments = [say(world.job, world.team.admin, f"Note {n}") for n in range(3)]
    client = client_for(world.team.admin)

    codes = [client.delete(comment_url(comment)).status_code for comment in comments]

    assert codes == [204, 204, 204]
    cache.clear()


def test_the_rate_is_declared(settings: Any) -> None:
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["job_comment"] == "30/min"


def test_listing_a_thread_costs_a_fixed_number_of_queries(
    world: World, client_for: ClientFor, django_assert_max_num_queries: Any
) -> None:
    authors = [world.a.user, world.b.user, world.team.admin, world.team.hm_a, world.quiet]
    for number in range(6):
        top = say(world.job, authors[number % 5], f"Question {number}")
        for answer in range(2):
            say(world.job, authors[(number + answer + 1) % 5], f"Answer {number}.{answer}", top)

    for user in (world.a.user, world.team.admin):
        client = client_for(user)
        with django_assert_max_num_queries(11):
            body = thread(client, world.job)
        assert len(body["comments"]) == 6
        assert sum(len(row["replies"]) for row in body["comments"]) == 12


def test_old_question_routes_are_gone(world: World, client_for: ClientFor) -> None:
    job = world.job
    admin = client_for(world.team.admin)
    recruiter = client_for(world.a.user)
    some_id = "7d7a41c0-5c47-4d33-a4f2-d0a1d1b8b6c1"

    assert admin.get(f"/api/v1/jobs/{job.pk}/questions").status_code == 404
    assert (
        admin.post(f"/api/v1/jobs/{job.pk}/questions", {"body": "x"}, format="json").status_code
        == 404
    )
    assert admin.delete(f"/api/v1/job-questions/{some_id}").status_code == 404
    assert (
        admin.post(f"/api/v1/job-questions/{some_id}/answer", {}, format="json").status_code == 404
    )
    assert recruiter.get(f"/api/v1/recruiters/me/jobs/{job.pk}/questions").status_code == 404
    assert (
        recruiter.post(
            f"/api/v1/recruiters/me/jobs/{job.pk}/questions", {"body": "x"}, format="json"
        ).status_code
        == 404
    )
