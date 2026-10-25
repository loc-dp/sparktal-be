from datetime import timedelta

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from apps.accounts.tests.factories import UserFactory
from apps.candidates.models import Stage
from apps.candidates.tests.factories import ApplicationFactory
from apps.hiring.models import (
    ApprovalRequest,
    ApprovalStatus,
    CandidateNote,
    Feedback,
    Interview,
    InterviewFormat,
    Placement,
    Verdict,
)


class InterviewFactory(DjangoModelFactory[Interview]):
    class Meta:
        model = Interview

    application = factory.SubFactory(ApplicationFactory)
    round = 1
    starts_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=2))
    ends_at = factory.LazyAttribute(lambda interview: interview.starts_at + timedelta(hours=1))
    format = InterviewFormat.ONLINE
    location = "https://meet.google.com/abc-defg-hij"


class FeedbackFactory(DjangoModelFactory[Feedback]):
    class Meta:
        model = Feedback

    interview = factory.SubFactory(InterviewFactory)
    author = factory.SubFactory(UserFactory)
    verdict = Verdict.YES
    body = "Clear thinker, explained trade-offs with numbers."


class CandidateNoteFactory(DjangoModelFactory[CandidateNote]):
    class Meta:
        model = CandidateNote

    application = factory.SubFactory(ApplicationFactory)
    author = factory.SubFactory(UserFactory)
    body = factory.Sequence(lambda n: f"Note {n}")


class ApprovalRequestFactory(DjangoModelFactory[ApprovalRequest]):
    class Meta:
        model = ApprovalRequest

    application = factory.SubFactory(ApplicationFactory)
    requested_by = factory.SubFactory(UserFactory)
    approver = factory.SubFactory(UserFactory)
    stage = factory.LazyAttribute(lambda request: request.application.stage)
    status = ApprovalStatus.PENDING


class PlacementFactory(DjangoModelFactory[Placement]):
    class Meta:
        model = Placement

    application = factory.SubFactory(ApplicationFactory, stage=Stage.ONBOARDED)
    start_date = factory.LazyFunction(timezone.localdate)
    guarantee_days = factory.LazyAttribute(
        lambda placement: placement.application.job.guarantee_days
    )
