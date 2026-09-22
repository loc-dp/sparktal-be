from datetime import timedelta

from django.core.files.base import ContentFile
from django.utils import timezone

from apps.chat.models import Channel, Message, MessageKind
from apps.chat.signals import ensure_channel
from apps.core.seed import (
    DEMO_EMAILS,
    SeedContext,
    register_resetter,
    register_seeder,
)
from apps.core.testing.pdf import make_pdf

type MessageSpec = tuple[int, str, str, str, str, str]

NORTHSTAR: list[MessageSpec] = [
    (
        74,
        "northstar",
        "Sharing a new profile for this role: Mai Gia Bảo. Node.js-heavy, "
        "but two years of Go at Haravan on the checkout service.",
        "",
        "",
        "CV_Mai_Gia_Bao.pdf",
    ),
    (
        73,
        "maianh.tran@velapay.vn",
        "Thanks! We'll shortlist first — the team prefers deeper Go experience for this level.",
        "",
        "",
        "",
    ),
    (
        50,
        "khoa.nguyen@velapay.vn",
        "Quoting my note from round 1 so we are aligned on Phạm Minh Quân:",
        "Note on candidate · Phạm Minh Quân",
        "Very solid on Kafka and Postgres, explained idempotency with real numbers. "
        "I want to probe ledger design in round 2.",
        "",
    ),
    (
        49,
        "northstar",
        "Great. I'll brief him on ledger and reconciliation topics — no specifics, just the areas.",
        "",
        "",
        "",
    ),
    (
        5,
        "northstar",
        "Phạm Minh Quân confirmed he can do round 2 on 22/09 in the morning. "
        "Could you send the invite with the Google Meet link?",
        "",
        "",
        "",
    ),
    (
        4,
        "northstar",
        "Also a heads-up: Tiki may put a counter-offer on the table next week, so speed matters.",
        "",
        "",
        "",
    ),
]

BLUEFIN: list[MessageSpec] = [
    (
        28,
        "bluefin",
        "Hoàng Bảo Ngọc was flagged as a duplicate. We did not know about the "
        "earlier submission — understood that the first submitter keeps the rights.",
        "",
        "",
        "",
    ),
    (
        27,
        "maianh.tran@velapay.vn",
        "Thanks for flagging it. Đặng Khánh Vy is moving well, feedback from round 1 is positive.",
        "",
        "",
        "",
    ),
]

RECRUITER_KEYS = {"northstar": "NorthStar Talent", "bluefin": "Bluefin Search"}


def _post(ctx: SeedContext, channel: Channel, spec: MessageSpec) -> None:
    hours_ago, author_key, body, quote_source, quote_text, attachment_name = spec
    if author_key in RECRUITER_KEYS:
        author = ctx.refs["recruiters"][RECRUITER_KEYS[author_key]].user
    else:
        author = ctx.refs["users"][author_key]
    if Message.objects.filter(channel=channel, author=author, body=body).exists():
        return

    attachment = None
    if attachment_name:
        attachment = ContentFile(
            make_pdf("Mai Gia Bảo\nNode.js Engineer · Haravan"),
            name=attachment_name,
        )
    message = Message.objects.create(
        channel=channel,
        author=author,
        kind=MessageKind.USER,
        body=body,
        quote_source=quote_source,
        quote_text=quote_text,
        attachment=attachment,
        attachment_name=attachment_name,
        attachment_size=attachment.size if attachment else 0,
    )
    Message.objects.filter(pk=message.pk).update(
        created_at=timezone.now() - timedelta(hours=hours_ago)
    )


@register_seeder(order=70)
def seed(ctx: SeedContext) -> None:
    job = ctx.refs["jobs"]["Senior Backend Engineer"]
    oldest = timezone.now() - timedelta(hours=80)
    channels: dict[str, Channel] = {}

    for key, script in (("northstar", NORTHSTAR), ("bluefin", BLUEFIN)):
        recruiter = ctx.refs["recruiters"][RECRUITER_KEYS[key]]
        channel, _ = ensure_channel(job, recruiter)
        Message.objects.filter(channel=channel, kind=MessageKind.SYSTEM).update(created_at=oldest)
        for spec in script:
            _post(ctx, channel, spec)
        newest = channel.messages.order_by("-created_at", "-id").first()
        Channel.objects.filter(pk=channel.pk).update(
            last_message_at=newest.created_at if newest else None
        )
        channel.refresh_from_db()
        channels[f"{job.title} · {recruiter.nickname}"] = channel

    ctx.refs["channels"] = channels


@register_resetter(order=70)
def reset() -> None:
    Channel.objects.filter(recruiter__user__email__in=DEMO_EMAILS).delete()
