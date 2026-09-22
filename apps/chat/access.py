from datetime import UTC, datetime

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
from apps.jobs.access import can_view_job, jobs_visible_to

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def channels_visible_to(user: User | None) -> QuerySet[Channel]:
    if user is None or not user.is_authenticated:
        return Channel.objects.none()
    if user.kind == UserKind.RECRUITER:
        return Channel.objects.filter(recruiter__user=user)
    return Channel.objects.filter(job__in=jobs_visible_to(user))


def can_access_channel(user: User | None, channel: Channel) -> bool:
    if user is None or not user.is_authenticated:
        return False
    if user.kind == UserKind.RECRUITER:
        return channel.recruiter.user_id == user.pk
    return can_view_job(user, channel.job)


def channel_audience(channel: Channel) -> list[User]:
    members = User.objects.filter(membership__company_id=channel.job.company_id).select_related(
        "membership"
    )
    audience = [member for member in members if can_view_job(member, channel.job)]
    recruiter_user = channel.recruiter.user
    if recruiter_user.is_active:
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
