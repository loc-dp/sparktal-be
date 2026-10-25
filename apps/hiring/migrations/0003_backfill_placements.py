from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    """Applications hired before placements existed get one, so the Hires and Earnings pages
    are not empty on day one. The hire moment stands in for the start date."""
    Application = apps.get_model("candidates", "Application")
    Placement = apps.get_model("hiring", "Placement")
    hired = Application.objects.filter(stage="onboarded", placement__isnull=True).select_related(
        "job"
    )
    Placement.objects.bulk_create(
        Placement(
            application=application,
            start_date=timezone.localtime(application.stage_changed_at).date(),
            guarantee_days=application.job.guarantee_days,
        )
        for application in hired
    )


class Migration(migrations.Migration):
    dependencies = [
        ("hiring", "0002_placement"),
        ("jobs", "0011_job_guarantee_days"),
    ]

    operations = [migrations.RunPython(backfill, reverse_code=migrations.RunPython.noop)]
