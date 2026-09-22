import django.contrib.postgres.fields
import django.contrib.postgres.indexes
import django.contrib.postgres.search
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("candidates", "0001_initial"),
        ("recruiters", "0002_recruiter_review"),
    ]

    operations = [
        migrations.AddField(
            model_name="candidate",
            name="current_salary",
            field=models.DecimalField(blank=True, decimal_places=0, max_digits=15, null=True),
        ),
        migrations.AddField(
            model_name="candidate",
            name="cv_file",
            field=models.FileField(blank=True, null=True, upload_to="pool-cvs/%Y/%m/"),
        ),
        migrations.AddField(
            model_name="candidate",
            name="cv_filename",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="candidate",
            name="dob",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="candidate",
            name="email_hash",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="candidate",
            name="expected_salary",
            field=models.DecimalField(blank=True, decimal_places=0, max_digits=15, null=True),
        ),
        migrations.AddField(
            model_name="candidate",
            name="is_expat",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="candidate",
            name="languages",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=24), blank=True, default=list, size=None
            ),
        ),
        migrations.AddField(
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
                    ("bd", "Binh Duong"),
                    ("dnai", "Dong Nai"),
                    ("bn", "Bac Ninh"),
                    ("other", "Other province"),
                ],
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="candidate",
            name="name_dob_hash",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="candidate",
            name="notes",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="candidate",
            name="phone_hash",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="candidate",
            name="search_vector",
            field=django.contrib.postgres.search.SearchVectorField(null=True),
        ),
        migrations.AddField(
            model_name="candidate",
            name="skills",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=40), blank=True, default=list, size=None
            ),
        ),
        migrations.AddField(
            model_name="candidate",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="candidate",
            name="years_experience",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="candidate",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_vector"], name="candidate_search_gin"
            ),
        ),
    ]
