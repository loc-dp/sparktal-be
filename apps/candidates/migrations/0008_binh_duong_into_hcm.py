from django.db import migrations, models


def binh_duong_into_hcm(apps, schema_editor) -> None:
    """Binh Duong merged into Ho Chi Minh City on 1 July 2025; its code is gone."""
    database = schema_editor.connection.alias
    apps.get_model("candidates", "Candidate").objects.using(database).filter(location="bd").update(
        location="hcm"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("candidates", "0007_candidate_number_bounds"),
    ]

    operations = [
        migrations.RunPython(binh_duong_into_hcm, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="candidate",
            name="location",
            field=models.CharField(
                blank=True,
                choices=[
                    ("hcm", "Ho Chi Minh City"),
                    ("hn", "Ha Noi"),
                    ("dn", "Da Nang"),
                    ("hp", "Hai Phong"),
                    ("ct", "Can Tho"),
                    ("dnai", "Dong Nai"),
                    ("bn", "Bac Ninh"),
                    ("other", "Other province"),
                ],
                max_length=8,
            ),
        ),
    ]
