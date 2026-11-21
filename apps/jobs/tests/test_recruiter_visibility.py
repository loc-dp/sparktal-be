"""What a headhunter may see and do on a job (spec §5, §9).

A job is theirs to see only when it is live (active, not deleted, with a bounty) and either on
the marketplace or already theirs (an active assignment). The marketplace list shows only what
is on the marketplace; joining through the marketplace needs the marketplace; a job they already
joined stays theirs (leave, submit, Commission Vault) however the channels change later.
"""

import threading
import time
from collections.abc import Callable
from typing import Any, cast

import pytest
import yaml
from django.core.files.base import ContentFile
from django.db import connection, transaction
from django.http import Http404
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.candidates.models import Application
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.chat import signals as chat_signals
from apps.chat.models import Channel
from apps.core.testing.pdf import make_pdf
from apps.jobs import api as jobs_api
from apps.jobs import distribution
from apps.jobs.access import (
    can_submit_to_job,
    is_assigned,
    jobs_joined_by,
    jobs_offered_to,
    jobs_open_to,
    live_jobs,
)
from apps.jobs.distribution import MARKETPLACE_OFF, DistributionError, set_marketplace
from apps.jobs.marketplace import marketplace_queryset
from apps.jobs.models import BountyType, Job, JobAssignment, JobStatus
from apps.jobs.questions import JOB_NOT_TAKING_QUESTIONS
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

ClientFor = Callable[[User], APIClient]

JOBS = "/api/v1/marketplace/jobs"


@pytest.fixture(autouse=True)
def marketplace_on(settings: Any) -> None:
    """These cases are about the marketplace channel; the ones for the flag off ask for
    `marketplace_off` below, which runs after this."""
    settings.MARKETPLACE_ENABLED = True


@pytest.fixture
def marketplace_off(settings: Any) -> None:
    settings.MARKETPLACE_ENABLED = False


def _no_bounty(job: Job) -> None:
    Job.objects.filter(pk=job.pk).update(bounty_type=None, bounty_pct=None, bounty_amount=None)


def _gone(case: str, recruiter: RecruiterProfile) -> Job:
    """A job the recruiter must not see, built the way `case` says."""
    fields: dict[str, Any] = {
        "pending": {"status": JobStatus.PENDING, "published_at": None},
        "closed": {"status": JobStatus.CLOSED},
        "no_bounty": {"bounty_type": None, "bounty_pct": None},
        "deleted": {"status": JobStatus.CLOSED, "deleted_at": timezone.now()},
        "direct_only_uninvited": {"on_marketplace": False},
    }[case]
    return cast(Job, JobFactory(**fields))


HIDDEN = [
    "pending",
    "no_bounty",
    "deleted",
    "closed",
    "direct_only_uninvited",
]


@pytest.mark.parametrize("case", HIDDEN)
def test_job_hidden_from_recruiter(
    case: str,
    client_for: ClientFor,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    job = _gone(case, recruiter_profile)
    candidate = CandidateFactory(owner=recruiter_profile)
    client = client_for(recruiter_user)

    listed = client.get(JOBS).json()["results"]
    assert str(job.id) not in {row["id"] for row in listed}
    assert client.get(f"{JOBS}/{job.id}").status_code == 404
    assert client.post(f"{JOBS}/{job.id}/join", {}, format="json").status_code == 404
    checked = client.post(
        f"/api/v1/jobs/{job.id}/submissions/check",
        {"candidate_id": str(candidate.pk)},
        format="json",
    )
    submitted = client.post(
        f"/api/v1/jobs/{job.id}/submissions",
        {"candidate_id": str(candidate.pk)},
        format="multipart",
    )
    # A job outside what they may see does not exist for them: 404, never 403.
    assert (checked.status_code, submitted.status_code) == (404, 404)
    assert not JobAssignment.objects.filter(job=job).exists()
    assert not Application.objects.filter(job=job).exists()


NOT_JOINED = "Join this job from the marketplace before you submit a candidate."
NOT_OPEN = "This job is no longer open for submissions."


def test_submit_on_a_marketplace_job_not_yet_joined_is_403(
    client_for: ClientFor,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
    settings: Any,
) -> None:
    """With the marketplace on they can see it but may not act on it until they join."""
    settings.MARKETPLACE_ENABLED = True
    job = cast(Job, JobFactory())
    candidate = CandidateFactory(owner=recruiter_profile)
    client = client_for(recruiter_user)

    checked = client.post(
        f"/api/v1/jobs/{job.id}/submissions/check",
        {"candidate_id": str(candidate.pk)},
        format="json",
    )
    submitted = client.post(
        f"/api/v1/jobs/{job.id}/submissions",
        {"candidate_id": str(candidate.pk)},
        format="multipart",
    )

    assert (checked.status_code, submitted.status_code) == (403, 403)
    assert checked.json() == {"detail": NOT_JOINED}
    assert not Application.objects.filter(job=job).exists()


@pytest.mark.parametrize("state", ["closed", "deleted", "no_bounty"])
def test_submit_on_a_joined_job_that_is_no_longer_live_is_403(
    state: str,
    client_for: ClientFor,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    """It is still in their Commission Vault, so they can see it: refused, not hidden."""
    fields: dict[str, Any] = {
        "closed": {"status": JobStatus.CLOSED},
        "deleted": {"status": JobStatus.CLOSED, "deleted_at": timezone.now()},
        "no_bounty": {"bounty_type": None, "bounty_pct": None},
    }
    job = cast(Job, JobFactory(**fields[state]))
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    candidate = CandidateFactory(owner=recruiter_profile)
    client = client_for(recruiter_user)

    checked = client.post(
        f"/api/v1/jobs/{job.id}/submissions/check",
        {"candidate_id": str(candidate.pk)},
        format="json",
    )
    submitted = client.post(
        f"/api/v1/jobs/{job.id}/submissions",
        {"candidate_id": str(candidate.pk)},
        format="multipart",
    )

    assert (checked.status_code, submitted.status_code) == (403, 403)
    assert checked.json() == {"detail": NOT_OPEN}
    assert not Application.objects.filter(job=job).exists()


def test_marketplace_join_twice_is_one_assignment(
    client_for: ClientFor, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    job = cast(Job, JobFactory())
    client = client_for(recruiter_user)

    first = client.post(f"{JOBS}/{job.id}/join", {}, format="json")
    second = client.post(f"{JOBS}/{job.id}/join", {}, format="json")

    assert (first.status_code, second.status_code) == (201, 200)
    assert JobAssignment.objects.filter(job=job, recruiter=recruiter_profile).count() == 1
    channel = Channel.objects.get(job=job, recruiter=recruiter_profile)
    assert first.json() == second.json()
    assert first.json()["channel_id"] == str(channel.id)


def test_leave_direct_only_job(
    client_for: ClientFor, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    job = cast(Job, JobFactory(on_marketplace=False))
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    client = client_for(recruiter_user)

    left = client.post(f"{JOBS}/{job.id}/leave", {}, format="json")

    assert left.status_code == 200
    assert left.json()["joined"] is False
    assert not jobs_joined_by(recruiter_profile).filter(pk=job.pk).exists()
    # Nobody is on it any more and nothing offers it to them: it is gone from their side.
    assert client.get(f"{JOBS}/{job.id}").status_code == 404


def test_leave_a_job_that_closed_since(
    client_for: ClientFor, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    job = cast(Job, JobFactory(status=JobStatus.CLOSED))
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)

    assert (
        client_for(recruiter_user).post(f"{JOBS}/{job.id}/leave", {}, format="json").status_code
        == 200
    )


def test_leave_a_job_never_joined_is_404(
    client_for: ClientFor, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    job = cast(Job, JobFactory())

    response = client_for(recruiter_user).post(f"{JOBS}/{job.id}/leave", {}, format="json")

    assert response.status_code == 404


def test_marketplace_off_keeps_existing_assignment(
    client_for: ClientFor, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    job = cast(Job, JobFactory())
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    other = cast(RecruiterProfile, RecruiterProfileFactory(user__email="bluefin@example.com"))
    set_marketplace(job, on=False)
    client = client_for(recruiter_user)
    job.refresh_from_db()

    assert can_submit_to_job(recruiter_profile, job)
    check = client.post(
        f"/api/v1/jobs/{job.id}/submissions/check",
        {"candidate_id": str(CandidateFactory(owner=recruiter_profile).pk)},
        format="json",
    )
    assert check.status_code == 200
    # Still theirs to open, though no longer on the list; a newcomer gets nothing.
    assert client.get(f"{JOBS}/{job.id}").json()["joined"] is True
    assert str(job.id) not in {row["id"] for row in client.get(JOBS).json()["results"]}
    assert not jobs_offered_to(other).filter(pk=job.pk).exists()


def test_commission_vault_keeps_deleted_job(
    client_for: ClientFor, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    job = cast(
        Job, JobFactory(title="Retired Role", status=JobStatus.CLOSED, deleted_at=timezone.now())
    )
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)

    rows = client_for(recruiter_user).get("/api/v1/recruiters/me/jobs").json()["results"]

    assert [row["title"] for row in rows] == ["Retired Role"]
    assert rows[0]["status"] == "closed"


# --- the one gate, in the helpers -------------------------------------------------------


def test_live_jobs_needs_active_undeleted_and_a_bounty() -> None:
    live = cast(Job, JobFactory())
    fixed = cast(
        Job,
        JobFactory(bounty_type=BountyType.FIXED, bounty_pct=None, bounty_amount=30_000_000),
    )
    JobFactory(status=JobStatus.PENDING)
    JobFactory(status=JobStatus.CLOSED)
    JobFactory(deleted_at=timezone.now())
    JobFactory(bounty_type=None, bounty_pct=None)
    JobFactory(bounty_type=BountyType.FIXED, bounty_pct=None, bounty_amount=None)

    assert set(live_jobs()) == {live, fixed}


def test_jobs_offered_to_is_live_assignments_and_the_marketplace_only_when_on(
    recruiter_profile: RecruiterProfile, settings: Any, marketplace_off: None
) -> None:
    on_market = cast(Job, JobFactory())
    joined = cast(Job, JobFactory(on_marketplace=False))
    JobAssignmentFactory(job=joined, recruiter=recruiter_profile)
    stranger = cast(Job, JobFactory(on_marketplace=False))
    JobAssignmentFactory(job=stranger)  # somebody else's assignment
    left = cast(Job, JobFactory(on_marketplace=False))
    JobAssignmentFactory(job=left, recruiter=recruiter_profile, left_at=timezone.now())
    # Everything above, on a job that is not live, drops out.
    closed = cast(Job, JobFactory(status=JobStatus.CLOSED))
    JobAssignmentFactory(job=closed, recruiter=recruiter_profile)

    assert set(jobs_offered_to(recruiter_profile)) == {joined}
    settings.MARKETPLACE_ENABLED = True
    offered = jobs_offered_to(recruiter_profile)
    assert set(offered) == {on_market, joined}
    assert offered.count() == 2
    assert list(jobs_offered_to(None)) == []


def test_marketplace_queryset_is_only_what_is_on_the_marketplace(
    recruiter_profile: RecruiterProfile,
) -> None:
    on_market = cast(Job, JobFactory())
    direct = cast(Job, JobFactory(on_marketplace=False))
    JobAssignmentFactory(job=direct, recruiter=recruiter_profile)
    JobFactory(bounty_type=None, bounty_pct=None)
    JobFactory(deleted_at=timezone.now())

    assert list(marketplace_queryset(recruiter_profile)) == [on_market]
    assert list(marketplace_queryset(None)) == []


def test_can_submit_needs_a_live_job_and_an_active_assignment(
    recruiter_profile: RecruiterProfile,
) -> None:
    job = cast(Job, JobFactory(on_marketplace=False))
    assert not can_submit_to_job(recruiter_profile, job)  # not joined
    assert not can_submit_to_job(None, job)
    assignment = JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    assert can_submit_to_job(recruiter_profile, job)  # the marketplace being off does not matter

    for change in (
        {"status": JobStatus.CLOSED},
        {"deleted_at": timezone.now()},
        {"bounty_type": None, "bounty_pct": None},
        {"bounty_type": BountyType.FIXED, "bounty_pct": None, "bounty_amount": None},
    ):
        Job.objects.filter(pk=job.pk).update(**change)
        job.refresh_from_db()
        assert not can_submit_to_job(recruiter_profile, job), change
        Job.objects.filter(pk=job.pk).update(
            status=JobStatus.ACTIVE,
            deleted_at=None,
            bounty_type=BountyType.PCT,
            bounty_pct=18,
            bounty_amount=None,
        )
        job.refresh_from_db()

    assignment.left_at = timezone.now()
    assignment.save(update_fields=["left_at"])
    assert not can_submit_to_job(recruiter_profile, job)


def test_joined_job_without_a_bounty_refuses_submissions(
    client_for: ClientFor, recruiter_user: User, recruiter_profile: RecruiterProfile
) -> None:
    """The assignment predates the rule (or an admin made it): the gate still holds."""
    job = cast(Job, JobFactory())
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    _no_bounty(job)
    candidate = CandidateFactory(owner=recruiter_profile)
    client = client_for(recruiter_user)

    check = client.post(
        f"/api/v1/jobs/{job.id}/submissions/check",
        {"candidate_id": str(candidate.pk)},
        format="json",
    )
    submit = client.post(
        f"/api/v1/jobs/{job.id}/submissions",
        {
            "candidate_id": str(candidate.pk),
            "reason_for_leaving": "Growth",
            "motivation": "Scope",
            "cv_file": ContentFile(make_pdf("CV"), name="cv.pdf"),
        },
        format="multipart",
    )

    assert check.status_code == 403
    assert submit.status_code == 403
    assert not Application.objects.filter(job=job).exists()
    # It still shows in My jobs: closing a bounty-less job off must not lose their rows.
    rows = client.get("/api/v1/recruiters/me/jobs").json()["results"]
    assert [row["id"] for row in rows] == [str(job.id)]


# --- the join path ---------------------------------------------------------------------


def test_marketplace_join_ensures_the_channel_once_in_the_service_and_not_in_the_view(
    client_for: ClientFor,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first join ensures the channel twice, both outside the view; the view adds none.

    The new assignment's post_save signal (which also posts the "joined" message) is the first
    and `join_job` is the second, the one that also covers a rejoin. Before this task the view
    made a third call just to read the channel id back.
    """
    calls = {"signal": 0, "join_job": 0, "view": 0}
    real = chat_signals.ensure_channel

    def spy(name: str) -> Callable[[Job, RecruiterProfile], Any]:
        def counted(job: Job, recruiter: RecruiterProfile) -> Any:
            calls[name] += 1
            return real(job, recruiter)

        return counted

    monkeypatch.setattr(chat_signals, "ensure_channel", spy("signal"))
    monkeypatch.setattr(distribution, "ensure_channel", spy("join_job"))
    # Fails if the view ever imports and calls it again (it has no such name today).
    monkeypatch.setattr(jobs_api, "ensure_channel", spy("view"), raising=False)
    job = cast(Job, JobFactory())

    response = client_for(recruiter_user).post(f"{JOBS}/{job.id}/join", {}, format="json")

    assert response.status_code == 201
    assert calls == {"signal": 1, "join_job": 1, "view": 0}


def test_marketplace_join_of_an_unknown_job_is_404(
    client_for: ClientFor, recruiter_user: User
) -> None:
    response = client_for(recruiter_user).post(
        f"{JOBS}/00000000-0000-4000-8000-000000000000/join", {}, format="json"
    )

    assert response.status_code == 404


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("change", ["closed", "marketplace_off"])
def test_marketplace_join_rechecks_the_job_once_it_holds_the_lock(
    change: str, recruiter_profile: RecruiterProfile
) -> None:
    """A close or marketplace-off that commits while the join waits for the job lock wins.

    One thread holds the job row, changes it and commits only after the join request is blocked
    on that row; a join that had looked the job up before waiting would then still go through.
    """
    job = cast(Job, JobFactory())
    holding, release = threading.Event(), threading.Event()
    seen: dict[str, Any] = {}
    changes = {
        "closed": {"status": JobStatus.CLOSED},
        "marketplace_off": {"on_marketplace": False},
    }

    def committer() -> None:
        try:
            with transaction.atomic():
                Job.objects.select_for_update().get(pk=job.pk)
                holding.set()
                release.wait(15)
                Job.objects.filter(pk=job.pk).update(**changes[change])
        finally:
            connection.close()

    def joiner() -> None:
        try:
            client = APIClient()
            client.force_login(recruiter_profile.user)
            seen["status"] = client.post(f"{JOBS}/{job.id}/join", {}, format="json").status_code
        finally:
            connection.close()

    first = threading.Thread(target=committer)
    second = threading.Thread(target=joiner)
    try:
        first.start()
        assert holding.wait(15)
        second.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
                if cursor.fetchone()[0]:
                    break
            time.sleep(0.05)
        else:
            pytest.fail("the join never waited for the job lock")
    finally:
        # Whatever happened above, let the holder commit and leave no thread behind.
        release.set()
        for thread in (first, second):
            if thread.is_alive():
                thread.join(15)

    assert seen.get("status") == 404
    assert not JobAssignment.objects.filter(job=job).exists()
    assert not Channel.objects.filter(job=job).exists()


# --- the headhunter schema is pinned ---------------------------------------------------


@pytest.fixture(scope="module")
def schema_components() -> dict[str, Any]:
    schema = SchemaGenerator().get_schema(request=None, public=True)
    return cast(dict[str, Any], yaml.safe_load(yaml.safe_dump(schema))["components"]["schemas"])


BOUNTY_TYPE_REF = {"$ref": "#/components/schemas/BountyTypeEnum"}


@pytest.mark.parametrize("name", ["MarketplaceJob", "MarketplaceJobListItem"])
def test_marketplace_schema_has_a_bounty_for_sure(
    name: str, schema_components: dict[str, Any]
) -> None:
    properties = schema_components[name]["properties"]

    assert BOUNTY_TYPE_REF in (
        properties["bounty_type"].get("allOf") or [properties["bounty_type"]]
    )
    assert not properties["bounty_type"].get("nullable")
    assert "oneOf" not in properties["bounty_type"]
    assert (properties["bounty_pct"]["minimum"], properties["bounty_pct"]["maximum"]) == (5, 30)
    assert properties["bounty_pct"]["nullable"] is True
    assert properties["bounty_amount"]["nullable"] is True
    assert {"bounty_type", "bounty_pct", "bounty_amount"} <= set(
        schema_components[name]["required"]
    )


@pytest.mark.parametrize("name", ["RecruiterJob", "WaitingJob"])
def test_joined_job_schema_may_have_no_bounty_type_but_never_blank(
    name: str, schema_components: dict[str, Any]
) -> None:
    properties = schema_components[name]["properties"]
    bounty_type = properties["bounty_type"]

    assert bounty_type["nullable"] is True
    branches = bounty_type.get("oneOf") or bounty_type.get("anyOf") or []
    assert BOUNTY_TYPE_REF in branches
    assert {"$ref": "#/components/schemas/BlankEnum"} not in branches
    assert (properties["bounty_pct"]["minimum"], properties["bounty_pct"]["maximum"]) == (5, 30)
    assert properties["bounty_pct"]["nullable"] is True
    assert properties["bounty_amount"]["nullable"] is True


# --- Headhunter visibility without invites: assigned, or already holding a CV on it.


@pytest.fixture
def live_job() -> Job:
    return cast(Job, JobFactory())


@pytest.fixture
def closed_job() -> Job:
    return cast(Job, JobFactory(status=JobStatus.CLOSED))


@pytest.fixture
def other_recruiter() -> RecruiterProfile:
    return cast(RecruiterProfile, RecruiterProfileFactory())


def _cv_on(job: Job, recruiter: RecruiterProfile) -> Application:
    return cast(
        Application,
        ApplicationFactory(job=job, candidate=CandidateFactory(owner=recruiter)),
    )


def test_assigned_job_is_open(recruiter_profile: RecruiterProfile, live_job: Job) -> None:
    JobAssignmentFactory(job=live_job, recruiter=recruiter_profile)

    assert list(jobs_open_to(recruiter_profile)) == [live_job]
    assert list(jobs_offered_to(recruiter_profile)) == [live_job]
    assert is_assigned(recruiter_profile, live_job)
    assert can_submit_to_job(recruiter_profile, live_job)


def test_unassigned_live_job_is_hidden(
    recruiter_profile: RecruiterProfile, live_job: Job, marketplace_off: None
) -> None:
    assert live_job.on_marketplace is True

    assert not jobs_open_to(recruiter_profile).filter(pk=live_job.pk).exists()
    assert not jobs_offered_to(recruiter_profile).filter(pk=live_job.pk).exists()
    assert not is_assigned(recruiter_profile, live_job)


def test_marketplace_flag_reopens_offer(
    recruiter_profile: RecruiterProfile, live_job: Job, settings: Any
) -> None:
    settings.MARKETPLACE_ENABLED = True

    assert jobs_offered_to(recruiter_profile).filter(pk=live_job.pk).exists()
    assert not jobs_open_to(recruiter_profile).filter(pk=live_job.pk).exists()


def test_removed_with_cv_still_opens_job(
    recruiter_profile: RecruiterProfile, live_job: Job
) -> None:
    JobAssignmentFactory(job=live_job, recruiter=recruiter_profile, left_at=timezone.now())
    _cv_on(live_job, recruiter_profile)

    assert jobs_open_to(recruiter_profile).filter(pk=live_job.pk).exists()
    assert not is_assigned(recruiter_profile, live_job)
    assert not can_submit_to_job(recruiter_profile, live_job)
    assert not jobs_joined_by(recruiter_profile).filter(pk=live_job.pk).exists()


def test_removed_without_cv_loses_job(recruiter_profile: RecruiterProfile, live_job: Job) -> None:
    JobAssignmentFactory(job=live_job, recruiter=recruiter_profile, left_at=timezone.now())

    assert not jobs_open_to(recruiter_profile).filter(pk=live_job.pk).exists()
    assert not is_assigned(recruiter_profile, live_job)


def test_closed_assigned_job_stays_open(
    recruiter_profile: RecruiterProfile, closed_job: Job
) -> None:
    JobAssignmentFactory(job=closed_job, recruiter=recruiter_profile)

    assert jobs_open_to(recruiter_profile).filter(pk=closed_job.pk).exists()
    assert not jobs_offered_to(recruiter_profile).filter(pk=closed_job.pk).exists()


def test_deleted_job_stays_open_to_assignee_and_cv_holder(
    recruiter_profile: RecruiterProfile, other_recruiter: RecruiterProfile
) -> None:
    job = cast(Job, JobFactory(status=JobStatus.CLOSED, deleted_at=timezone.now()))
    JobAssignmentFactory(job=job, recruiter=recruiter_profile)
    _cv_on(job, other_recruiter)

    assert jobs_open_to(recruiter_profile).filter(pk=job.pk).exists()
    assert jobs_open_to(other_recruiter).filter(pk=job.pk).exists()
    assert not jobs_offered_to(recruiter_profile).filter(pk=job.pk).exists()


def test_another_recruiters_cv_does_not_open_job(
    recruiter_profile: RecruiterProfile, other_recruiter: RecruiterProfile, live_job: Job
) -> None:
    _cv_on(live_job, other_recruiter)

    assert not jobs_open_to(recruiter_profile).filter(pk=live_job.pk).exists()
    assert jobs_open_to(other_recruiter).filter(pk=live_job.pk).exists()


def test_assignment_and_cvs_list_the_job_once(
    recruiter_profile: RecruiterProfile, live_job: Job
) -> None:
    JobAssignmentFactory(job=live_job, recruiter=recruiter_profile)
    _cv_on(live_job, recruiter_profile)
    _cv_on(live_job, recruiter_profile)

    assert list(jobs_open_to(recruiter_profile)) == [live_job]


def test_no_recruiter_opens_nothing(live_job: Job) -> None:
    assert list(jobs_open_to(None)) == []
    assert not is_assigned(None, live_job)


# --- The marketplace is closed while MARKETPLACE_ENABLED is off.

MARKETPLACE_URLS = [
    ("get", "/api/v1/marketplace/jobs"),
    ("get", "/api/v1/marketplace/jobs/{job}"),
    ("post", "/api/v1/marketplace/jobs/{job}/join"),
    ("post", "/api/v1/marketplace/jobs/{job}/leave"),
]


@pytest.mark.parametrize(("method", "url"), MARKETPLACE_URLS)
def test_marketplace_urls_404_when_off(
    method: str,
    url: str,
    recruiter_client: APIClient,
    recruiter_profile: RecruiterProfile,
    live_job: Job,
    marketplace_off: None,
) -> None:
    path = url.format(job=live_job.pk)

    for client in (recruiter_client, APIClient()):
        assert getattr(client, method)(path).status_code == 404

    assert JobAssignment.objects.count() == 0


def test_marketplace_queryset_is_empty_when_off(
    recruiter_profile: RecruiterProfile, live_job: Job, marketplace_off: None
) -> None:
    assert live_job.on_marketplace is True

    assert list(marketplace_queryset(recruiter_profile)) == []

    with pytest.raises(Http404):
        distribution.join_marketplace_job(live_job.pk, recruiter_profile)
    assert JobAssignment.objects.count() == 0


def test_set_marketplace_on_is_refused_when_off_and_off_still_works(
    live_job: Job, settings: Any, marketplace_off: None
) -> None:
    with pytest.raises(DistributionError) as refused:
        set_marketplace(live_job, on=True)
    assert str(refused.value.detail) == MARKETPLACE_OFF == "The marketplace is switched off."

    assert set_marketplace(live_job, on=False).on_marketplace is False

    settings.MARKETPLACE_ENABLED = True
    assert set_marketplace(live_job, on=True).on_marketplace is True


def test_assign_log_line_carries_the_actor_pk_and_no_email(
    live_job: Job,
    recruiter_profile: RecruiterProfile,
    caplog: pytest.LogCaptureFixture,
) -> None:
    actor = cast(User, UserFactory(email="ops-person@sparktal.example"))

    with caplog.at_level("INFO", logger="apps.jobs.distribution"):
        distribution.assign_recruiters(live_job, actor=actor, recruiters=[recruiter_profile])

    assert str(actor.pk) in caplog.text
    assert actor.email not in caplog.text


# --- Q&A moved out from under the marketplace gate.


@pytest.fixture
def assigned_live_job(live_job: Job, recruiter_profile: RecruiterProfile) -> Job:
    JobAssignmentFactory(job=live_job, recruiter=recruiter_profile)
    return live_job


@pytest.fixture
def removed_with_cv_job(live_job: Job, recruiter_profile: RecruiterProfile) -> Job:
    JobAssignmentFactory(job=live_job, recruiter=recruiter_profile, left_at=timezone.now())
    _cv_on(live_job, recruiter_profile)
    return live_job


def test_questions_route_moved(recruiter_client: APIClient, assigned_live_job: Job) -> None:
    new = f"/api/v1/recruiters/me/jobs/{assigned_live_job.pk}/questions"
    old = f"/api/v1/marketplace/jobs/{assigned_live_job.pk}/questions"

    assert recruiter_client.get(new).status_code == 200
    assert recruiter_client.post(new, {"body": "Is it hybrid?"}).status_code == 201
    assert recruiter_client.get(old).status_code == 404
    assert recruiter_client.post(old, {"body": "Is it hybrid?"}).status_code == 404


def test_removed_recruiter_with_cv_reads_but_cannot_ask(
    recruiter_client: APIClient, removed_with_cv_job: Job
) -> None:
    url = f"/api/v1/recruiters/me/jobs/{removed_with_cv_job.pk}/questions"

    assert recruiter_client.get(url).status_code == 200
    refused = recruiter_client.post(url, {"body": "Still hiring?"})

    assert refused.status_code == 403
    assert refused.json() == {"detail": JOB_NOT_TAKING_QUESTIONS}


def test_unassigned_recruiter_cannot_read_or_ask(
    recruiter_client: APIClient, live_job: Job
) -> None:
    url = f"/api/v1/recruiters/me/jobs/{live_job.pk}/questions"

    assert recruiter_client.get(url).status_code == 404
    assert recruiter_client.post(url, {"body": "Hello?"}).status_code == 404
