from django.db import migrations, models


class Migration(migrations.Migration):
    """Added after the jobs backfill so existing HM/TA rows already have a team."""

    dependencies = [
        ("companies", "0004_teams"),
        ("jobs", "0006_backfill_teams"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="companymembership",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("can_publish", False), ("role", "admin"), ("team__isnull", True)),
                    models.Q(
                        ("can_publish", False), ("role", "hiring_manager"), ("team__isnull", False)
                    ),
                    models.Q(("role", "talent_acquisition"), ("team__isnull", False)),
                    _connector="OR",
                ),
                name="membership_team_matches_role",
            ),
        ),
    ]
