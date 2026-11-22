from typing import cast

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import User
from apps.candidates.models import Application, Stage
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.chat.access import (
    GENERAL_READ_ONLY,
    can_access_channel,
    can_post_to_channel,
    channel_audience,
    channels_visible_to,
    with_unread_count,
)
from apps.chat.models import Channel, ChannelRead
from apps.chat.tests.factories import ChannelFactory, MessageFactory
from apps.companies.models import Company
from apps.jobs.models import Job, JobAssignment, JobStatus
from apps.jobs.tests.conftest import TeamWorld, team_world  # noqa: F401  (team_world: the fixture)
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory
from apps.recruiters.models import RecruiterProfile
from apps.recruiters.tests.factories import RecruiterProfileFactory

pytestmark = pytest.mark.django_db


def test_admin_and_ta_see_every_channel_of_their_teams_jobs(
    company: Company, company_admin: User, company_ta: User
) -> None:
    channel = ChannelFactory(job=JobFactory(company=company, created_by=company_ta))
    assert list(channels_visible_to(company_admin)) == [channel]
    assert list(channels_visible_to(company_ta)) == [channel]


def test_hiring_manager_only_sees_channels_of_their_teams_jobs(
    company: Company, company_hm: User, company_ta: User
) -> None:
    ours = ChannelFactory(job=JobFactory(company=company, team=company_hm.membership.team))
    hidden = ChannelFactory(job=JobFactory(company=company, created_by=company_ta))
    assert list(channels_visible_to(company_hm)) == [ours]
    assert can_access_channel(company_hm, ours)
    assert not can_access_channel(company_hm, hidden)


def test_recruiter_lists_only_own_channels(
    company: Company, recruiter_profile: RecruiterProfile, recruiter_user: User
) -> None:
    """Two headhunters on one job: each reaches their own general and candidate threads only."""
    job = JobFactory(company=company)
    rival = RecruiterProfileFactory(nickname="Bluefin Search")
    mine = [_assigned_channel(job, recruiter_profile), _submitted(job, recruiter_profile).channel]
    theirs = [_assigned_channel(job, rival), _submitted(job, rival).channel]
    assert set(channels_visible_to(recruiter_user)) == set(mine)
    for channel in mine:
        assert can_access_channel(recruiter_user, channel)
        assert can_post_to_channel(recruiter_user, channel)
    for channel in theirs:
        assert not can_access_channel(recruiter_user, channel)
        assert not can_post_to_channel(recruiter_user, channel)
        assert recruiter_user not in channel_audience(channel)


def test_another_recruiters_channel_on_another_job_is_out_of_reach(
    company: Company, recruiter_profile: RecruiterProfile, recruiter_user: User
) -> None:
    mine = ChannelFactory(job=JobFactory(company=company), recruiter=recruiter_profile)
    someone_elses = ChannelFactory(job=JobFactory(company=company))
    assert list(channels_visible_to(recruiter_user)) == [mine]
    assert can_access_channel(recruiter_user, mine)
    assert not can_access_channel(recruiter_user, someone_elses)


def test_other_companies_see_nothing(company: Company, other_company_admin: User) -> None:
    channel = ChannelFactory(job=JobFactory(company=company))
    assert list(channels_visible_to(other_company_admin)) == []
    assert not can_access_channel(other_company_admin, channel)
    assert not can_post_to_channel(other_company_admin, channel)


def test_signed_out_reaches_nothing(company: Company) -> None:
    channel = ChannelFactory(job=JobFactory(company=company))
    assert not can_access_channel(None, channel)
    assert not can_post_to_channel(None, channel)
    assert list(channels_visible_to(None)) == []


def test_company_user_sees_team_job_channels_only(team_world: TeamWorld) -> None:  # noqa: F811
    """Candidate and general threads of a team's job reach that team and the admin, nobody else."""
    world = team_world
    candidate_a = world.application_a.channel
    general_a = _assigned_channel(world.job_a, world.recruiter)
    general_b = _assigned_channel(world.job_b, world.recruiter)
    team_a = {candidate_a, general_a}
    assert set(channels_visible_to(world.admin)) == {*team_a, general_b}
    assert set(channels_visible_to(world.hm_a)) == team_a
    assert set(channels_visible_to(world.hm_b)) == {general_b}
    for channel in team_a:
        assert can_access_channel(world.hm_a, channel)
        assert can_post_to_channel(world.hm_a, channel)
        assert not can_access_channel(world.hm_b, channel)
        assert not can_post_to_channel(world.hm_b, channel)
        assert world.hm_b not in channel_audience(channel)


def test_audience_is_every_member_who_may_view_the_job_plus_the_recruiter(
    company: Company,
    company_admin: User,
    company_ta: User,
    company_hm: User,
    recruiter_user: User,
    recruiter_profile: RecruiterProfile,
) -> None:
    channel = ChannelFactory(
        job=JobFactory(company=company, created_by=company_ta), recruiter=recruiter_profile
    )
    audience = set(channel_audience(channel))
    assert audience == {company_admin, company_ta, recruiter_user}
    assert company_hm not in audience


def test_unread_counts_messages_from_the_other_side_only(
    company: Company, company_ta: User, recruiter_profile: RecruiterProfile
) -> None:
    channel = ChannelFactory(
        job=JobFactory(company=company, created_by=company_ta), recruiter=recruiter_profile
    )
    MessageFactory(channel=channel, author=recruiter_profile.user)
    MessageFactory(channel=channel, author=recruiter_profile.user)
    MessageFactory(channel=channel, author=company_ta)
    MessageFactory(channel=channel, author=None)
    row = with_unread_count(Channel.objects.all(), company_ta).get(pk=channel.pk)
    assert row.unread_count == 3


def test_unread_is_zero_after_marking_read(
    company: Company, company_ta: User, recruiter_profile: RecruiterProfile
) -> None:
    channel = ChannelFactory(
        job=JobFactory(company=company, created_by=company_ta), recruiter=recruiter_profile
    )
    MessageFactory(channel=channel, author=recruiter_profile.user)
    channel.refresh_from_db()
    ChannelRead.objects.create(
        channel=channel, user=company_ta, last_read_at=channel.last_message_at
    )
    assert with_unread_count(Channel.objects.all(), company_ta).get(pk=channel.pk).unread_count == 0
    MessageFactory(channel=channel, author=recruiter_profile.user)
    assert with_unread_count(Channel.objects.all(), company_ta).get(pk=channel.pk).unread_count == 1


def test_audience_of_a_candidate_thread(
    company: Company,
    company_admin: User,
    company_ta: User,
    company_hm: User,
    recruiter_profile: RecruiterProfile,
    recruiter_user: User,
) -> None:
    """Job viewers plus the headhunter who submitted the CV; not another headhunter on the job."""
    job = JobFactory(company=company, created_by=company_ta)
    rival = RecruiterProfileFactory(nickname="Bluefin Search")
    _assigned_channel(job, rival)
    _submitted(job, rival)
    _assigned_channel(job, recruiter_profile)
    thread = _submitted(job, recruiter_profile).channel
    assert set(channel_audience(thread)) == {company_admin, company_ta, recruiter_user}
    assert company_hm not in channel_audience(thread)


def test_closed_job_and_rejected_candidate_still_post(
    company: Company, company_ta: User, recruiter_profile: RecruiterProfile, recruiter_user: User
) -> None:
    job = JobFactory(company=company, created_by=company_ta)
    general = _assigned_channel(job, recruiter_profile)
    application = _submitted(job, recruiter_profile)
    Application.objects.filter(pk=application.pk).update(stage=Stage.REJECTED)
    Job.objects.filter(pk=job.pk).update(status=JobStatus.CLOSED)
    for channel in (general, application.channel):
        channel.refresh_from_db()
        channel.job.refresh_from_db()
        for user in (company_ta, recruiter_user):
            assert can_post_to_channel(user, channel), (user, channel.kind)


# --- A headhunter's general thread lives with their hold on the job (spec 5.4); their candidate
# --- threads are always theirs.


def _assigned_channel(job: Job, profile: RecruiterProfile) -> Channel:
    """The real flow: assigning opens the (job, headhunter) general thread."""
    JobAssignmentFactory(job=job, recruiter=profile)
    return Channel.objects.get(job=job, recruiter=profile, application=None)


def _submitted(job: Job, profile: RecruiterProfile) -> Application:
    """The real flow: submitting a CV opens its candidate thread."""
    return cast(
        Application,
        ApplicationFactory(job=job, candidate=CandidateFactory(owner=profile), recruiter=profile),
    )


def _remove(job: Job, profile: RecruiterProfile) -> None:
    JobAssignment.objects.filter(job=job, recruiter=profile).update(left_at=timezone.now())


def test_assigned_headhunter_keeps_their_channel(
    company: Company, recruiter_profile: RecruiterProfile, recruiter_user: User
) -> None:
    channel = _assigned_channel(JobFactory(company=company), recruiter_profile)
    assert list(channels_visible_to(recruiter_user)) == [channel]
    assert can_access_channel(recruiter_user, channel)
    assert can_post_to_channel(recruiter_user, channel)
    assert recruiter_user in channel_audience(channel)


def test_removed_without_cv_loses_channel_everywhere(
    company: Company, recruiter_profile: RecruiterProfile, recruiter_user: User
) -> None:
    job = JobFactory(company=company)
    channel = _assigned_channel(job, recruiter_profile)
    _remove(job, recruiter_profile)
    assert list(channels_visible_to(recruiter_user)) == []
    assert not can_access_channel(recruiter_user, channel)
    assert not can_post_to_channel(recruiter_user, channel)
    assert recruiter_user not in channel_audience(channel)


def test_removed_recruiter_general_thread_rules(
    company: Company, recruiter_profile: RecruiterProfile, recruiter_user: User
) -> None:
    """Removed with a CV on the job: the general thread turns read-only, the CV's thread does not.

    Removed with no CV on another job: that job's general thread is gone from their list and
    from the socket audience.
    """
    job = JobFactory(company=company)
    general = _assigned_channel(job, recruiter_profile)
    thread = _submitted(job, recruiter_profile).channel
    empty_job = JobFactory(company=company)
    lost = _assigned_channel(empty_job, recruiter_profile)
    _remove(job, recruiter_profile)
    _remove(empty_job, recruiter_profile)

    assert set(channels_visible_to(recruiter_user)) == {general, thread}
    assert can_access_channel(recruiter_user, general)
    assert not can_post_to_channel(recruiter_user, general)
    assert recruiter_user in channel_audience(general)
    assert can_access_channel(recruiter_user, thread)
    assert can_post_to_channel(recruiter_user, thread)
    assert recruiter_user in channel_audience(thread)

    assert lost not in channels_visible_to(recruiter_user)
    assert not can_access_channel(recruiter_user, lost)
    assert recruiter_user not in channel_audience(lost)


def test_read_only_sentence() -> None:
    assert GENERAL_READ_ONLY == (
        "You are no longer assigned to this job, so this conversation is read-only."
    )


def test_inactive_recruiter_is_left_out_of_the_audience(
    company: Company, recruiter_profile: RecruiterProfile, recruiter_user: User
) -> None:
    job = JobFactory(company=company)
    _assigned_channel(job, recruiter_profile)
    thread = _submitted(job, recruiter_profile).channel
    User.objects.filter(pk=recruiter_user.pk).update(is_active=False)
    thread = Channel.objects.select_related("recruiter__user").get(pk=thread.pk)
    assert recruiter_user not in channel_audience(thread)


def test_company_side_is_unchanged_by_a_removal(
    company: Company, company_admin: User, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company)
    channel = _assigned_channel(job, recruiter_profile)
    _remove(job, recruiter_profile)
    assert list(channels_visible_to(company_admin)) == [channel]
    assert can_access_channel(company_admin, channel)
    assert can_post_to_channel(company_admin, channel)
    assert company_admin in channel_audience(channel)


def test_headhunter_channel_filter_is_one_query_however_many_channels(
    company: Company, recruiter_profile: RecruiterProfile, recruiter_user: User
) -> None:
    for _ in range(3):
        job = JobFactory(company=company)
        _assigned_channel(job, recruiter_profile)
        _submitted(job, recruiter_profile)
    with CaptureQueriesContext(connection) as queries:
        assert len(channels_visible_to(recruiter_user)) == 6
    assert len(queries) <= 2  # the one profile lookup, then one filtered select
