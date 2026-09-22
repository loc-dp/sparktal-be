import pytest

from apps.candidates.tests.factories import ApplicationFactory, CandidateFactory
from apps.chat.models import Channel, MessageKind
from apps.companies.models import Company
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db


def test_first_application_opens_a_channel_with_a_system_message(
    company: Company, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company)
    candidate = CandidateFactory(owner=recruiter_profile, full_name="Phạm Minh Quân")
    ApplicationFactory(job=job, candidate=candidate, recruiter=recruiter_profile)
    channel = Channel.objects.get(job=job, recruiter=recruiter_profile)
    message = channel.messages.get()
    assert message.kind == MessageKind.SYSTEM
    assert message.body == f"{recruiter_profile.nickname} submitted Phạm Minh Quân"


def test_a_second_application_reuses_the_channel(
    company: Company, recruiter_profile: RecruiterProfile
) -> None:
    job = JobFactory(company=company)
    ApplicationFactory(job=job, recruiter=recruiter_profile)
    ApplicationFactory(job=job, recruiter=recruiter_profile)
    channel = Channel.objects.get(job=job, recruiter=recruiter_profile)
    assert channel.messages.count() == 2


def test_updating_an_application_posts_nothing(
    company: Company, recruiter_profile: RecruiterProfile
) -> None:
    application = ApplicationFactory(job=JobFactory(company=company), recruiter=recruiter_profile)
    channel = Channel.objects.get(job=application.job, recruiter=recruiter_profile)
    application.stage = "screen"
    application.save(update_fields=["stage"])
    assert channel.messages.count() == 1


def test_two_recruiters_on_one_job_get_two_channels(company: Company) -> None:
    job = JobFactory(company=company)
    ApplicationFactory(job=job)
    ApplicationFactory(job=job)
    assert Channel.objects.filter(job=job).count() == 2
