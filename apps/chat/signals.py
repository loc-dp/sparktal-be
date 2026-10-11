from typing import Any

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.candidates.models import Application
from apps.chat.models import Channel, Message, MessageKind
from apps.jobs.models import Job, JobAssignment
from apps.recruiters.models import RecruiterProfile


def ensure_channel(job: Job, recruiter: RecruiterProfile) -> tuple[Channel, bool]:
    return Channel.objects.get_or_create(job=job, recruiter=recruiter)


@receiver(
    post_save,
    sender=Application,
    dispatch_uid="chat.create_channel_for_application",
)
def create_channel_for_application(
    sender: type[Application],
    instance: Application,
    created: bool,
    **kwargs: Any,
) -> None:
    if not created:
        return
    channel, _ = ensure_channel(instance.job, instance.recruiter)
    Message.objects.create(
        channel=channel,
        application=instance,
        author=None,
        kind=MessageKind.SYSTEM,
        body=(f"{instance.recruiter.nickname} submitted {instance.candidate.full_name}"),
    )


@receiver(post_save, sender=JobAssignment, dispatch_uid="chat.create_channel_for_assignment")
def create_channel_for_assignment(
    sender: type[JobAssignment], instance: JobAssignment, created: bool, **kwargs: Any
) -> None:
    if not created:
        return
    channel, _ = ensure_channel(instance.job, instance.recruiter)
    Message.objects.create(
        channel=channel,
        author=None,
        kind=MessageKind.SYSTEM,
        body=f"{instance.recruiter.nickname} joined this job",
    )
