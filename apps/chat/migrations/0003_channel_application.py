import django.db.models.deletion
from django.db import migrations, models
from django.db.models import OuterRef, Subquery

SET_CONSTRAINTS_IMMEDIATE = "SET CONSTRAINTS ALL IMMEDIATE"


def _set_last_message_at(Channel, Message):
    """Every channel's `last_message_at` is the newest of its own messages, or empty."""
    newest = Message.objects.filter(channel_id=OuterRef("pk")).order_by("-created_at")
    Channel.objects.update(last_message_at=Subquery(newest.values("created_at")[:1]))


def split_channels(apps, schema_editor):
    """Give every application its own thread and move the messages about it there.

    Messages with no application stay in the general channel of (job, recruiter). A new
    thread starts with a copy of each read marker the general channel has, at the same time,
    and nobody without a marker there gets one: a message keeps its `created_at` and reads
    as unread in its thread exactly when it did before, so the unread total does not change.

    A second run changes nothing: an application that has a thread keeps it and its markers,
    and only messages outside their application's thread move.
    """
    Application = apps.get_model("candidates", "Application")
    Channel = apps.get_model("chat", "Channel")
    ChannelRead = apps.get_model("chat", "ChannelRead")
    Message = apps.get_model("chat", "Message")
    applications = Application.objects.order_by("submitted_at", "id").values(
        "id", "job_id", "recruiter_id"
    )
    for application in applications.iterator():
        pair = {"job_id": application["job_id"], "recruiter_id": application["recruiter_id"]}
        thread, created = Channel.objects.get_or_create(
            application_id=application["id"], defaults=pair
        )
        Message.objects.filter(application_id=application["id"]).exclude(
            channel_id=thread.pk
        ).update(channel_id=thread.pk)
        if not created:
            continue
        markers = ChannelRead.objects.filter(
            channel__job_id=pair["job_id"],
            channel__recruiter_id=pair["recruiter_id"],
            channel__application__isnull=True,
        ).values("user_id", "last_read_at")
        ChannelRead.objects.bulk_create(
            ChannelRead(channel_id=thread.pk, **marker) for marker in markers
        )
    _set_last_message_at(Channel, Message)


def merge_channels(apps, schema_editor):
    """Move every thread's messages back to the general channel of its (job, recruiter),
    making that channel if it is gone, then delete the threads (and their read markers)."""
    Channel = apps.get_model("chat", "Channel")
    Message = apps.get_model("chat", "Message")
    threads = list(
        Channel.objects.filter(application__isnull=False).values("id", "job_id", "recruiter_id")
    )
    for thread in threads:
        general, _ = Channel.objects.get_or_create(
            job_id=thread["job_id"], recruiter_id=thread["recruiter_id"], application=None
        )
        Message.objects.filter(channel_id=thread["id"]).update(channel_id=general.pk)
    Channel.objects.filter(pk__in=[thread["id"] for thread in threads]).delete()
    _set_last_message_at(Channel, Message)


class Migration(migrations.Migration):
    dependencies = [
        ("candidates", "0010_cv_parse_reuse"),
        ("chat", "0002_message_application"),
        # jobs 0012 creates the (job, recruiter) channels this migration splits; it must run first.
        ("jobs", "0012_invites_become_assignments"),
    ]

    operations = [
        migrations.AddField(
            model_name="channel",
            name="application",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="channel",
                to="candidates.application",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="channel",
            name="chat_channel_job_recruiter",
        ),
        # The data step leaves deferred foreign-key checks pending on `chat_channel`, and Postgres
        # refuses to alter a table that has them: run the checks before the constraint changes
        # that follow the data step, going forward (below) and backward (here).
        migrations.RunSQL(migrations.RunSQL.noop, reverse_sql=SET_CONSTRAINTS_IMMEDIATE),
        migrations.RunPython(split_channels, reverse_code=merge_channels),
        migrations.RunSQL(SET_CONSTRAINTS_IMMEDIATE, reverse_sql=migrations.RunSQL.noop),
        migrations.AddConstraint(
            model_name="channel",
            constraint=models.UniqueConstraint(
                condition=models.Q(application__isnull=True),
                fields=("job", "recruiter"),
                name="chat_general_channel_job_recruiter",
            ),
        ),
    ]
