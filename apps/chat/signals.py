from typing import Any

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.candidates.models import Application
from apps.chat.models import Channel, Message, MessageKind
from apps.jobs.models import Job, JobAssignment
from apps.recruiters.models import RecruiterProfile


def ensure_channel(job: Job, recruiter: RecruiterProfile) -> tuple[Channel, bool]:
    """The general thread of (job, recruiter), made if missing.

    Race safe: a second caller's insert hits the partial unique constraint
    `chat_general_channel_job_recruiter`, which `get_or_create` takes inside its own savepoint and
    answers by reading the row the first caller made, so the caller's transaction goes on.
    """
    return Channel.objects.get_or_create(job=job, recruiter=recruiter, application=None)


def ensure_candidate_channel(application: Application) -> tuple[Channel, bool]:
    """The thread about this one submitted candidate, made if missing.

    Race safe the same way as `ensure_channel`, on the one-to-one `Channel.application`.
    """
    return Channel.objects.get_or_create(
        application=application,
        defaults={"job": application.job, "recruiter": application.recruiter},
    )


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
    channel, _ = ensure_candidate_channel(instance)
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
