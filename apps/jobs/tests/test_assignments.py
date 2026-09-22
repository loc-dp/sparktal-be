import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.chat.models import Channel, MessageKind
from apps.jobs.models import Job, JobAssignment
from apps.jobs.tests.factories import JobFactory
from apps.recruiters.models import RecruiterProfile

pytestmark = pytest.mark.django_db


def test_assignment_opens_one_channel(job: Job, recruiter_profile: RecruiterProfile) -> None:
    assignment = JobAssignment.objects.create(job=job, recruiter=recruiter_profile)

    assert assignment.is_active
    channel = Channel.objects.get(job=job, recruiter=recruiter_profile)
    assert channel.messages.get().kind == MessageKind.SYSTEM
    assert channel.messages.get().body == f"{recruiter_profile.nickname} joined this job"


def test_assignment_is_unique_and_rejoin_preserves_channel(
    recruiter_profile: RecruiterProfile,
) -> None:
    job = JobFactory()
    assignment = JobAssignment.objects.create(job=job, recruiter=recruiter_profile)
    with pytest.raises(IntegrityError), transaction.atomic():
        JobAssignment.objects.create(job=job, recruiter=recruiter_profile)
    assignment.left_at = timezone.now()
    assignment.save(update_fields=["left_at"])
    assert not assignment.is_active
    assignment.left_at = None
    assignment.save(update_fields=["left_at"])
    assert Channel.objects.get(job=job, recruiter=recruiter_profile).messages.count() == 1
