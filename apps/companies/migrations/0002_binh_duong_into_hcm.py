from django.db import migrations, models


def binh_duong_into_hcm(apps, schema_editor) -> None:
    """Binh Duong merged into Ho Chi Minh City on 1 July 2025; its code is gone."""
    database = schema_editor.connection.alias
    apps.get_model("companies", "Office").objects.using(database).filter(city="bd").update(
        city="hcm"
    )
    apps.get_model("companies", "Company").objects.using(database).filter(located="bd").update(
        located="hcm"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(binh_duong_into_hcm, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="company",
            name="located",
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
        migrations.AlterField(
            model_name="historicalcompany",
            name="located",
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
        migrations.AlterField(
            model_name="office",
            name="city",
            field=models.CharField(
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
                default="hcm",
                max_length=8,
            ),
        ),
    ]
