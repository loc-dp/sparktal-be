from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.chat.models import Channel, Message
from apps.hiring.models import ApprovalRequest, ApprovalStatus
from apps.hiring.tests.factories import ApprovalRequestFactory, InterviewFactory

pytestmark = pytest.mark.django_db


def test_interview_round_is_unique_per_application(application: Any) -> None:
    InterviewFactory(application=application, round=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        InterviewFactory(application=application, round=1)


def test_interview_must_end_after_it_starts(application: Any) -> None:
    start = timezone.now()
    with pytest.raises(IntegrityError), transaction.atomic():
        InterviewFactory(application=application, starts_at=start, ends_at=start)


def test_only_one_pending_approval_per_application(
    application: Any, company_ta: Any, company_admin: Any
) -> None:
    ApprovalRequestFactory(application=application, requested_by=company_ta, approver=company_admin)
    with pytest.raises(IntegrityError), transaction.atomic():
        ApprovalRequestFactory(
            application=application, requested_by=company_ta, approver=company_admin
        )
    ApprovalRequest.objects.update(status=ApprovalStatus.DECLINED)
    ApprovalRequestFactory(application=application, requested_by=company_ta, approver=company_admin)
    assert application.approval_requests.count() == 2


def test_message_can_be_tagged_with_an_application(application: Any) -> None:
    channel = Channel.objects.get(job=application.job, recruiter=application.recruiter)
    Message.objects.create(channel=channel, application=application, body="About Quân")
    assert application.messages.filter(body="About Quân").count() == 1
