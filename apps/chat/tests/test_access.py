import pytest

from apps.accounts.models import User
from apps.chat.access import (
    can_access_channel,
    channel_audience,
    channels_visible_to,
    with_unread_count,
)
from apps.chat.models import Channel, ChannelRead
from apps.chat.tests.factories import ChannelFactory, MessageFactory
from apps.companies.models import Company
from apps.jobs.access import grant_creator_perms
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db


def test_admin_and_ta_see_every_channel_of_their_company(
    company: Company, company_admin: User, company_ta: User
) -> None:
    channel = ChannelFactory(job=JobFactory(company=company))
    assert list(channels_visible_to(company_admin)) == [channel]
    assert list(channels_visible_to(company_ta)) == [channel]


def test_hiring_manager_only_sees_channels_of_granted_jobs(
    company: Company, company_hm: User
) -> None:
    mine = JobFactory(company=company, created_by=company_hm)
    grant_creator_perms(mine, company_hm)
    granted = ChannelFactory(job=mine)
    hidden = ChannelFactory(job=JobFactory(company=company))
    assert list(channels_visible_to(company_hm)) == [granted]
    assert can_access_channel(company_hm, granted)
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
    channel = ChannelFactory(job=JobFactory(company=company), recruiter=recruiter_profile)
    audience = set(channel_audience(channel))
    assert audience == {company_admin, company_ta, recruiter_user}
    assert company_hm not in audience


def test_unread_counts_messages_from_the_other_side_only(
    company: Company, company_ta: User, recruiter_profile: RecruiterProfile
) -> None:
    channel = ChannelFactory(job=JobFactory(company=company), recruiter=recruiter_profile)
    MessageFactory(channel=channel, author=recruiter_profile.user)
    MessageFactory(channel=channel, author=recruiter_profile.user)
    MessageFactory(channel=channel, author=company_ta)
    MessageFactory(channel=channel, author=None)
    row = with_unread_count(Channel.objects.all(), company_ta).get(pk=channel.pk)
    assert row.unread_count == 3


def test_unread_is_zero_after_marking_read(
    company: Company, company_ta: User, recruiter_profile: RecruiterProfile
) -> None:
    channel = ChannelFactory(job=JobFactory(company=company), recruiter=recruiter_profile)
    MessageFactory(channel=channel, author=recruiter_profile.user)
    channel.refresh_from_db()
    ChannelRead.objects.create(
        channel=channel, user=company_ta, last_read_at=channel.last_message_at
    )
    assert with_unread_count(Channel.objects.all(), company_ta).get(pk=channel.pk).unread_count == 0
    MessageFactory(channel=channel, author=recruiter_profile.user)
    assert with_unread_count(Channel.objects.all(), company_ta).get(pk=channel.pk).unread_count == 1
