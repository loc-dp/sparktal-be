import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import User
from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.chat.access import (
    can_access_channel,
    channel_audience,
    channels_visible_to,
    with_unread_count,
)
from apps.chat.models import Channel, ChannelRead
from apps.chat.tests.factories import ChannelFactory, MessageFactory
from apps.companies.models import Company
from apps.jobs.models import Job, JobAssignment
from apps.jobs.tests.factories import JobAssignmentFactory, JobFactory
from apps.recruiters.models import RecruiterProfile

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


def test_recruiter_only_sees_their_own_channels(
    company: Company,
    recruiter_profile: RecruiterProfile,
    recruiter_user: User,
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


# --- A headhunter reaches a channel only while its job is open to them (assign-01 final review).


def _assigned_channel(job: Job, profile: RecruiterProfile) -> Channel:
    """The real flow: assigning opens the (job, headhunter) channel."""
    JobAssignmentFactory(job=job, recruiter=profile)
    return Channel.objects.get(job=job, recruiter=profile)


def _remove(job: Job, profile: RecruiterProfile) -> None:
    JobAssignment.objects.filter(job=job, recruiter=profile).update(left_at=timezone.now())


def test_assigned_headhunter_keeps_their_channel(
    company: Company, recruiter_profile: RecruiterProfile, recruiter_user: User
) -> None:
    channel = _assigned_channel(JobFactory(company=company), recruiter_profile)
    assert list(channels_visible_to(recruiter_user)) == [channel]
    assert can_access_channel(recruiter_user, channel)
    assert recruiter_user in channel_audience(channel)


def test_removed_without_cv_loses_channel_everywhere(
    company: Company, recruiter_profile: RecruiterProfile, recruiter_user: User
) -> None:
    job = JobFactory(company=company)
    channel = _assigned_channel(job, recruiter_profile)
    _remove(job, recruiter_profile)
    assert list(channels_visible_to(recruiter_user)) == []
    assert not can_access_channel(recruiter_user, channel)
    assert recruiter_user not in channel_audience(channel)


def test_removed_with_cv_still_sees_the_channel(
    company: Company, recruiter_profile: RecruiterProfile, recruiter_user: User
) -> None:
    job = JobFactory(company=company)
    channel = _assigned_channel(job, recruiter_profile)
    ApplicationFactory(job=job, candidate=CandidateFactory(owner=recruiter_profile))
    _remove(job, recruiter_profile)
    assert list(channels_visible_to(recruiter_user)) == [channel]
    assert can_access_channel(recruiter_user, channel)
    assert recruiter_user in channel_audience(channel)


def test_company_side_is_unchanged_by_a_removal(
    company: Company, company_admin: User, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company)
    channel = _assigned_channel(job, recruiter_profile)
    _remove(job, recruiter_profile)
    assert list(channels_visible_to(company_admin)) == [channel]
    assert can_access_channel(company_admin, channel)
    assert company_admin in channel_audience(channel)


def test_headhunter_channel_filter_is_one_query_however_many_channels(
    company: Company, recruiter_profile: RecruiterProfile, recruiter_user: User
) -> None:
    for _ in range(6):
        _assigned_channel(JobFactory(company=company), recruiter_profile)
    with CaptureQueriesContext(connection) as queries:
        assert len(channels_visible_to(recruiter_user)) == 6
    assert len(queries) <= 2  # the one profile lookup, then one filtered select
