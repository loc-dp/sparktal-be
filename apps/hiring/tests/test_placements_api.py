"""The placement endpoints: the company's Hires, the recruiter's Earnings, and the detail blocks."""

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, cast

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory
from apps.hiring.models import Placement, PlacementOutcome
from apps.hiring.placements import (
    LEFT_WINDOW,
    NO_SHOW_WINDOW,
    NOT_STARTING,
    START_DATE_FROM_TODAY,
)
from apps.hiring.tests.factories import PlacementFactory
from apps.jobs.models import Job, JobStatus
from apps.jobs.tests.conftest import TeamWorld
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db

ClientFor = Callable[[User], APIClient]

LIST = "/api/v1/company/placements"
RECRUITER_FIELDS = {
    "company_name",
    "bounty_type",
    "bounty_pct",
    "bounty_amount",
    "payout_status",
    "paid_amount",
    "paid_at",
}
COMPANY_FIELDS = {
    "id",
    "application_id",
    "candidate_name",
    "job",
    "recruiter_nickname",
    "start_date",
    "guarantee_days",
    "guarantee_ends_on",
    "days_left",
    "status",
    "outcome",
    "ended_on",
    "outcome_note",
    "can_change_start_date",
    "can_report_no_show",
    "can_report_left",
}


def today() -> date:
    return timezone.localdate()


def placement_on(job: Job, *, days_from_today: int = 0, **extra: Any) -> Placement:
    """A placement on `job` starting `days_from_today` days from today (negative: in the past)."""
    application = cast(Application, ApplicationFactory(job=job, stage=Stage.ONBOARDED))
    start = today() + timedelta(days=days_from_today)
    return cast(
        Placement,
        PlacementFactory(application=application, start_date=start, guarantee_days=60, **extra),
    )


def url(placement: Placement, tail: str = "") -> str:
    return f"/api/v1/placements/{placement.pk}{tail}"


def test_company_list_filters_by_status_and_job(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    starting = placement_on(team_world.job_a, days_from_today=5)
    in_guarantee = placement_on(team_world.job_a, days_from_today=-10)
    passed = placement_on(team_world.job_b, days_from_today=-100)
    no_show = placement_on(team_world.job_b, days_from_today=-3, outcome=PlacementOutcome.NO_SHOW)
    left = placement_on(team_world.job_a, days_from_today=-20, outcome=PlacementOutcome.LEFT)
    client = client_for(team_world.admin)

    def ids(query: str) -> list[str]:
        response = client.get(f"{LIST}{query}")
        assert response.status_code == 200, response.content
        return [row["id"] for row in response.json()["results"]]

    everything = ids("")
    assert everything == [
        str(p.pk) for p in (starting, no_show, in_guarantee, left, passed)
    ]  # newest start date first
    assert client.get(LIST).json()["count"] == 5
    assert ids("?status=starting") == [str(starting.pk)]
    assert ids("?status=in_guarantee") == [str(in_guarantee.pk)]
    assert ids("?status=passed") == [str(passed.pk)]
    assert ids("?status=ended") == [str(no_show.pk), str(left.pk)]
    assert set(ids(f"?job={team_world.job_b.pk}")) == {str(passed.pk), str(no_show.pk)}
    assert ids(f"?job={team_world.job_b.pk}&status=ended") == [str(no_show.pk)]
    assert client.get(f"{LIST}?status=bogus").status_code == 400
    assert client.get(f"{LIST}?job=nope").status_code == 400


def test_company_list_only_shows_jobs_the_member_sees(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    mine = placement_on(team_world.job_a)
    placement_on(team_world.job_b)

    response = client_for(team_world.hm_a).get(LIST)

    assert [row["id"] for row in response.json()["results"]] == [str(mine.pk)]


def test_company_list_is_for_company_members(recruiter_client: APIClient) -> None:
    assert recruiter_client.get(LIST).status_code == 404


def test_company_payload_shape(team_world: TeamWorld, client_for: ClientFor) -> None:
    placement = placement_on(team_world.job_a, days_from_today=-10)

    row = client_for(team_world.admin).get(LIST).json()["results"][0]

    assert set(row) == COMPANY_FIELDS
    assert row["application_id"] == str(placement.application_id)
    assert row["candidate_name"] == placement.application.candidate.full_name
    assert row["job"] == {
        "id": str(team_world.job_a.pk),
        "title": team_world.job_a.title,
        "status": "active",
    }
    assert row["recruiter_nickname"] == placement.application.recruiter.nickname
    assert (row["status"], row["outcome"], row["days_left"]) == ("in_guarantee", "", 50)
    assert row["guarantee_ends_on"] == str(placement.start_date + timedelta(days=60))
    assert (row["can_change_start_date"], row["can_report_no_show"], row["can_report_left"]) == (
        False,
        False,
        True,
    )


def test_member_outside_team_gets_404(team_world: TeamWorld, client_for: ClientFor) -> None:
    placement = placement_on(team_world.job_a, days_from_today=3)
    client = client_for(team_world.hm_b)

    assert (
        client.patch(url(placement), {"start_date": str(today())}, format="json").status_code == 404
    )
    assert (
        client.post(url(placement, "/report"), {"outcome": "no_show"}, format="json").status_code
        == 404
    )
    assert client.patch(url(placement), {"start_date": "garbage"}, format="json").status_code == 404
    placement.refresh_from_db()
    assert placement.outcome == ""
    assert placement.start_date == today() + timedelta(days=3)


def test_recruiter_cannot_use_the_company_commands(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    placement = placement_on(team_world.job_a, days_from_today=3)

    client = client_for(team_world.recruiter.user)

    assert (
        client.patch(url(placement), {"start_date": str(today())}, format="json").status_code == 404
    )
    assert (
        client.post(url(placement, "/report"), {"outcome": "no_show"}, format="json").status_code
        == 404
    )


def test_change_start_date_only_before_start(team_world: TeamWorld, client_for: ClientFor) -> None:
    client = client_for(team_world.admin)
    starting = placement_on(team_world.job_a, days_from_today=5)
    new_date = today() + timedelta(days=9)

    response = client.patch(url(starting), {"start_date": str(new_date)}, format="json")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == COMPANY_FIELDS
    assert body["start_date"] == str(new_date)
    starting.refresh_from_db()
    assert starting.start_date == new_date

    # Out of range: before today, and more than a year ahead.
    for bad in (today() - timedelta(days=1), today() + timedelta(days=366)):
        response = client.patch(url(starting), {"start_date": str(bad)}, format="json")
        assert response.status_code == 409
        assert response.json() == {"detail": START_DATE_FROM_TODAY}

    # Once the candidate started, the date is fixed.
    started = placement_on(team_world.job_a, days_from_today=0)
    response = client.patch(
        url(started), {"start_date": str(today() + timedelta(days=2))}, format="json"
    )
    assert response.status_code == 409
    assert response.json() == {"detail": NOT_STARTING}
    # And so is a settled one.
    settled = placement_on(team_world.job_a, days_from_today=4, outcome=PlacementOutcome.NO_SHOW)
    response = client.patch(
        url(settled), {"start_date": str(today() + timedelta(days=6))}, format="json"
    )
    assert response.status_code == 409
    assert response.json() == {"detail": NOT_STARTING}


def test_change_start_date_needs_a_date(team_world: TeamWorld, client_for: ClientFor) -> None:
    placement = placement_on(team_world.job_a, days_from_today=5)

    response = client_for(team_world.admin).patch(url(placement), {}, format="json")

    assert response.status_code == 400
    assert "start_date" in response.json()


def test_report_no_show_on_start_date_ok_after_conflict(
    team_world: TeamWorld, client_for: ClientFor, mailoutbox: list[Any]
) -> None:
    client = client_for(team_world.admin)
    on_the_day = placement_on(team_world.job_a, days_from_today=0)
    day_after = placement_on(team_world.job_a, days_from_today=-1)

    late = client.post(url(day_after, "/report"), {"outcome": "no_show"}, format="json")
    ok = client.post(
        url(on_the_day, "/report"), {"outcome": "no_show", "note": "Never showed up"}, format="json"
    )

    assert late.status_code == 409
    assert late.json() == {"detail": NO_SHOW_WINDOW}
    day_after.refresh_from_db()
    assert day_after.outcome == ""
    assert ok.status_code == 200
    body = ok.json()
    assert (body["status"], body["outcome"], body["ended_on"]) == (
        "no_show",
        "no_show",
        str(today()),
    )
    assert body["outcome_note"] == "Never showed up"
    assert body["days_left"] == 0
    on_the_day.refresh_from_db()
    assert on_the_day.outcome == PlacementOutcome.NO_SHOW
    assert on_the_day.ended_on == on_the_day.start_date
    assert on_the_day.reported_by == team_world.admin
    assert on_the_day.reported_at is not None
    # The recruiter hears about it, and is sent to their earnings.
    assert [message.to for message in mailoutbox] == [[on_the_day.application.recruiter.user.email]]
    assert "/r/earnings" in mailoutbox[0].body


def test_report_no_show_before_the_start_is_fine(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    placement = placement_on(team_world.job_a, days_from_today=7)

    response = client_for(team_world.admin).post(
        url(placement, "/report"), {"outcome": "no_show"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["ended_on"] == str(placement.start_date)


def test_report_left_needs_date_and_note(
    team_world: TeamWorld, client_for: ClientFor, mailoutbox: list[Any]
) -> None:
    client = client_for(team_world.admin)
    placement = placement_on(team_world.job_a, days_from_today=-10)
    left_on = today() - timedelta(days=4)

    no_date = client.post(
        url(placement, "/report"), {"outcome": "left", "note": "Quit"}, format="json"
    )
    no_note = client.post(
        url(placement, "/report"), {"outcome": "left", "ended_on": str(left_on)}, format="json"
    )
    too_long = client.post(
        url(placement, "/report"),
        {"outcome": "left", "ended_on": str(left_on), "note": "x" * 1001},
        format="json",
    )
    before_start = client.post(
        url(placement, "/report"),
        {
            "outcome": "left",
            "ended_on": str(placement.start_date - timedelta(days=1)),
            "note": "Quit",
        },
        format="json",
    )
    in_the_future = client.post(
        url(placement, "/report"),
        {"outcome": "left", "ended_on": str(today() + timedelta(days=1)), "note": "Quit"},
        format="json",
    )
    ok = client.post(
        url(placement, "/report"),
        {"outcome": "left", "ended_on": str(left_on), "note": "Took another offer"},
        format="json",
    )

    assert no_date.status_code == 400
    assert "ended_on" in no_date.json()
    assert no_note.status_code == 400
    assert "note" in no_note.json()
    assert too_long.status_code == 400
    assert "note" in too_long.json()
    for window in (before_start, in_the_future):
        assert window.status_code == 409
        assert window.json() == {"detail": LEFT_WINDOW}
    assert ok.status_code == 200
    body = ok.json()
    assert (body["status"], body["outcome"], body["ended_on"]) == ("left", "left", str(left_on))
    assert body["outcome_note"] == "Took another offer"
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [placement.application.recruiter.user.email]


def test_report_left_only_during_the_guarantee(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    client = client_for(team_world.admin)
    starting = placement_on(team_world.job_a, days_from_today=3)
    passed = placement_on(team_world.job_a, days_from_today=-90)
    body = {"outcome": "left", "ended_on": str(today()), "note": "Quit"}

    for placement in (starting, passed):
        response = client.post(url(placement, "/report"), body, format="json")
        assert response.status_code == 409
        assert response.json() == {"detail": LEFT_WINDOW}
        placement.refresh_from_db()
        assert placement.outcome == ""


def test_report_unknown_outcome_is_a_400(team_world: TeamWorld, client_for: ClientFor) -> None:
    placement = placement_on(team_world.job_a)

    response = client_for(team_world.admin).post(
        url(placement, "/report"), {"outcome": "retired"}, format="json"
    )

    assert response.status_code == 400
    assert "outcome" in response.json()


def test_report_twice_conflicts(
    team_world: TeamWorld, client_for: ClientFor, mailoutbox: list[Any]
) -> None:
    client = client_for(team_world.admin)
    no_show = placement_on(team_world.job_a, days_from_today=0)
    left = placement_on(team_world.job_a, days_from_today=-10)
    left_body = {"outcome": "left", "ended_on": str(today() - timedelta(days=2)), "note": "Quit"}
    assert (
        client.post(url(no_show, "/report"), {"outcome": "no_show"}, format="json").status_code
        == 200
    )
    assert client.post(url(left, "/report"), left_body, format="json").status_code == 200
    sent = len(mailoutbox)

    again_no_show = client.post(url(no_show, "/report"), {"outcome": "no_show"}, format="json")
    again_left = client.post(url(left, "/report"), left_body, format="json")
    flip = client.post(url(left, "/report"), {"outcome": "no_show"}, format="json")

    assert (again_no_show.status_code, again_no_show.json()) == (409, {"detail": NO_SHOW_WINDOW})
    assert (again_left.status_code, again_left.json()) == (409, {"detail": LEFT_WINDOW})
    assert (flip.status_code, flip.json()) == (409, {"detail": NO_SHOW_WINDOW})
    left.refresh_from_db()
    assert left.outcome == PlacementOutcome.LEFT
    assert len(mailoutbox) == sent  # never a second write, never a second email


def test_recruiter_sees_only_own_with_summary(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile, joined_job: Job
) -> None:
    other = cast(RecruiterProfile, RecruiterProfileFactory())
    JobAssignmentFactory(job=joined_job, recruiter=other)

    def mine(days: int, **extra: Any) -> Placement:
        application = cast(
            Application,
            ApplicationFactory(job=joined_job, recruiter=recruiter_profile, stage=Stage.ONBOARDED),
        )
        return cast(
            Placement,
            PlacementFactory(
                application=application,
                start_date=today() + timedelta(days=days),
                guarantee_days=60,
                **extra,
            ),
        )

    starting = mine(4)
    in_guarantee = mine(-20)
    due = mine(-70)
    paid = mine(-100, paid_at=today() - timedelta(days=3), paid_amount=12_000_000)
    paid_too = mine(-120, paid_at=today() - timedelta(days=9), paid_amount=8_000_000)
    ended = mine(-30, outcome=PlacementOutcome.LEFT)
    theirs = cast(
        Placement,
        PlacementFactory(
            application=ApplicationFactory(job=joined_job, recruiter=other, stage=Stage.ONBOARDED),
            start_date=today(),
            guarantee_days=60,
        ),
    )

    response = recruiter_client.get("/api/v1/recruiters/me/placements")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "paid_total": 20_000_000,
        "due_count": 1,
        "in_guarantee_count": 1,
        "starting_count": 1,
    }
    ids = [row["id"] for row in body["results"]]
    assert ids == [str(p.pk) for p in (starting, in_guarantee, ended, due, paid, paid_too)]
    assert str(theirs.pk) not in ids
    row = next(row for row in body["results"] if row["id"] == str(paid.pk))
    assert set(row) == COMPANY_FIELDS | RECRUITER_FIELDS
    assert row["company_name"] == joined_job.company.name
    assert (row["bounty_type"], row["bounty_pct"]) == (
        joined_job.bounty_type,
        joined_job.bounty_pct,
    )
    assert (row["payout_status"], row["paid_amount"], row["paid_at"]) == (
        "paid",
        "12000000",
        str(today() - timedelta(days=3)),
    )
    statuses = {r["id"]: r["payout_status"] for r in body["results"]}
    assert statuses[str(due.pk)] == "due"
    assert statuses[str(ended.pk)] == "void"
    assert statuses[str(starting.pk)] == "not_due"


def test_recruiter_summary_is_zero_without_placements(recruiter_client: APIClient) -> None:
    response = recruiter_client.get("/api/v1/recruiters/me/placements")

    assert response.json() == {
        "summary": {"paid_total": 0, "due_count": 0, "in_guarantee_count": 0, "starting_count": 0},
        "results": [],
    }


def test_recruiter_placements_need_an_approved_recruiter(
    team_world: TeamWorld, client_for: ClientFor, pending_recruiter_user: User
) -> None:
    url_ = "/api/v1/recruiters/me/placements"

    assert client_for(team_world.admin).get(url_).status_code == 403
    assert client_for(pending_recruiter_user).get(url_).status_code == 403
    assert APIClient().get(url_).status_code in (401, 403)


def test_application_detail_carries_the_placement(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    placement = placement_on(team_world.job_a, days_from_today=-10)
    client = client_for(team_world.admin)

    body = client.get(f"/api/v1/applications/{placement.application_id}").json()
    plain = client.get(f"/api/v1/applications/{team_world.application_a.pk}").json()

    assert set(body["placement"]) == COMPANY_FIELDS
    assert body["placement"]["id"] == str(placement.pk)
    assert plain["placement"] is None


def test_the_hire_command_answers_with_the_placement(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    application = team_world.application_a
    Application.objects.filter(pk=application.pk).update(stage=Stage.OFFER)
    start = today() + timedelta(days=14)

    response = client_for(team_world.admin).post(
        f"/api/v1/applications/{application.pk}/hire", {"start_date": str(start)}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["placement"]["start_date"] == str(start)


def test_recruiter_submission_detail_carries_the_placement(
    recruiter_client: APIClient, recruiter_profile: RecruiterProfile, joined_job: Job
) -> None:
    hired = cast(
        Application,
        ApplicationFactory(job=joined_job, recruiter=recruiter_profile, stage=Stage.ONBOARDED),
    )
    placement = PlacementFactory(application=hired, start_date=today(), guarantee_days=60)
    open_one = cast(Application, ApplicationFactory(job=joined_job, recruiter=recruiter_profile))

    body = recruiter_client.get(f"/api/v1/recruiters/me/submissions/{hired.pk}").json()
    plain = recruiter_client.get(f"/api/v1/recruiters/me/submissions/{open_one.pk}").json()

    assert set(body["placement"]) == COMPANY_FIELDS | RECRUITER_FIELDS
    assert body["placement"]["id"] == str(placement.pk)
    assert plain["placement"] is None


def test_company_job_detail_and_marketplace_show_guarantee_days(
    team_world: TeamWorld, client_for: ClientFor
) -> None:
    job = cast(
        Job,
        JobFactory(
            company=team_world.company,
            team=team_world.team_a,
            status=JobStatus.ACTIVE,
            on_marketplace=True,
            guarantee_days=45,
        ),
    )
    JobAssignmentFactory(job=job, recruiter=team_world.recruiter)

    company_side = client_for(team_world.admin).get(f"/api/v1/jobs/{job.pk}")
    recruiter_side = client_for(team_world.recruiter.user).get(f"/api/v1/marketplace/jobs/{job.pk}")

    assert company_side.json()["guarantee_days"] == 45
    assert recruiter_side.status_code == 200
    assert recruiter_side.json()["guarantee_days"] == 45


def test_company_cannot_set_guarantee_days(team_world: TeamWorld, client_for: ClientFor) -> None:
    response = client_for(team_world.admin).patch(
        f"/api/v1/jobs/{team_world.job_a.pk}", {"guarantee_days": 5}, format="json"
    )

    assert response.status_code == 200
    team_world.job_a.refresh_from_db()
    assert team_world.job_a.guarantee_days == 60
