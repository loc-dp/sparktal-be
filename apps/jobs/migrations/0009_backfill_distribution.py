from django.db import migrations
from django.db.models.functions import Coalesce


def backfill(apps, schema_editor):
    """Spec §11: jobs already out keep their marketplace channel and get a posting time."""
    Job = apps.get_model("jobs", "Job")
    # Spec decision 11: Sparktal enters the bounty before it distributes. A job still with the
    # company or waiting in the queue carries the bounty its company chose (or the old default
    # of 15%), which the admin "Distribute" action would publish as if Sparktal had set it.
    # Active and closed jobs keep theirs. `update()` writes no history row, as intended.
    Job.objects.filter(status__in=("draft", "submitted", "pending")).update(
        bounty_type=None, bounty_pct=None, bounty_amount=None
    )
    # Closed jobs were on the marketplace before they closed, and reopening puts them back.
    Job.objects.filter(status__in=("active", "closed")).update(on_marketplace=True)
    Job.objects.filter(status__in=("pending", "active", "closed"), posted_at__isnull=True).update(
        posted_at=Coalesce("published_at", "created_at")
    )


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0008_job_distribution"),
    ]

    operations = [migrations.RunPython(backfill, reverse_code=migrations.RunPython.noop)]
