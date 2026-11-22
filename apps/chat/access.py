from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import (
    Count,
    DateTimeField,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
)
from django.db.models.functions import Coalesce

from apps.accounts.models import User, UserKind
from apps.chat.models import Channel, ChannelRead, Message
from apps.jobs.access import can_view_job, is_assigned, jobs_open_to, jobs_visible_to
from apps.jobs.models import JobAssignment
from apps.recruiters.models import RecruiterProfile

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Shown when a headhunter who was taken off the job writes in its general thread.
GENERAL_READ_ONLY = "You are no longer assigned to this job, so this conversation is read-only."


def _recruiter_of(user: User) -> RecruiterProfile | None:
    try:
        return user.recruiter
    except ObjectDoesNotExist:
        return None


def channels_visible_to(user: User | None) -> QuerySet[Channel]:
    """A headhunter: their own candidate threads always (one exists only because they submitted
    that CV), and their own general threads on jobs still open to them. Anyone else: every
    channel of the jobs they can see."""
    if user is None or not user.is_authenticated:
        return Channel.objects.none()
    if user.kind == UserKind.RECRUITER:
        return Channel.objects.filter(recruiter__user=user).filter(
            Q(application__isnull=False) | Q(job__in=jobs_open_to(_recruiter_of(user)))
        )
    return Channel.objects.filter(job__in=jobs_visible_to(user))


def can_access_channel(user: User | None, channel: Channel) -> bool:
    """`channels_visible_to` for one row."""
    if user is None or not user.is_authenticated:
        return False
    if user.kind == UserKind.RECRUITER:
        if channel.recruiter.user_id != user.pk:
            return False
        return channel.application_id is not None or _job_open_to(channel)
    return can_view_job(user, channel.job)


def can_post_to_channel(user: User | None, channel: Channel) -> bool:
    """Who may open a channel may write in it, whatever the job's status or the candidate's
    stage, except a headhunter in a general thread once they are no longer assigned."""
    if not can_access_channel(user, channel):
        return False
    assert user is not None, "can_access_channel only passes a signed-in user"
    if user.kind == UserKind.RECRUITER and channel.application_id is None:
        return is_assigned(channel.recruiter, channel.job)
    return True


def can_post_to_channels(user: User, channels: Iterable[Channel]) -> dict[UUID, bool]:
    """`can_post_to_channel` for rows that came out of `channels_visible_to(user)`, in at most
    one query however many rows there are (a headhunter's list would otherwise cost two queries
    per general thread)."""
    rows = list(channels)
    if user.kind != UserKind.RECRUITER:
        return {channel.pk: True for channel in rows}
    held = set(
        JobAssignment.objects.filter(
            recruiter__user=user,
            left_at__isnull=True,
            job_id__in={c.job_id for c in rows if c.application_id is None},
        ).values_list("job_id", flat=True)
    )
    return {c.pk: c.application_id is not None or c.job_id in held for c in rows}


def _job_open_to(channel: Channel) -> bool:
    """Its job is open to the channel's headhunter (assigned, or one of their CVs is on it)."""
    return jobs_open_to(channel.recruiter).filter(pk=channel.job_id).exists()


def channel_audience(channel: Channel) -> list[User]:
    """Who gets a socket event for a message in `channel`: everyone who may open it."""
    members = User.objects.filter(membership__company_id=channel.job.company_id).select_related(
        "membership"
    )
    audience = [member for member in members if can_view_job(member, channel.job)]
    recruiter_user = channel.recruiter.user
    if recruiter_user.is_active and can_access_channel(recruiter_user, channel):
        audience.append(recruiter_user)
    return audience


def with_unread_count(queryset: QuerySet[Channel], user: User) -> QuerySet[Channel]:
    last_read = Coalesce(
        Subquery(
            ChannelRead.objects.filter(channel=OuterRef(OuterRef("pk")), user_id=user.pk).values(
                "last_read_at"
            )[:1]
        ),
        Value(EPOCH, output_field=DateTimeField()),
    )
    unread = (
        Message.objects.filter(channel=OuterRef("pk"), created_at__gt=last_read)
        .filter(Q(author__isnull=True) | ~Q(author_id=user.pk))
        .order_by()
        .values("channel")
        .annotate(total=Count("pk"))
        .values("total")
    )
    return queryset.annotate(
        unread_count=Coalesce(Subquery(unread, output_field=IntegerField()), Value(0))
    )
