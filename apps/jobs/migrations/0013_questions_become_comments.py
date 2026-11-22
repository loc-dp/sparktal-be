import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def questions_to_comments(apps, schema_editor):
    """Each question becomes its headhunter's top-level comment and its answer a company reply,
    both dated when they were written; a hidden question is a hidden comment.

    `created_at` is `auto_now_add`, which `create()` overwrites, so each row is dated with a
    follow-up `update()`. An answer that is only whitespace is no answer. One with no
    `answered_at` is dated like its question, so it never reads as new. The asker always has
    a user: `asked_by` and the profile's `user` both cascade, so a question cannot outlive
    either. An answerer whose account is gone leaves a reply without an author.
    """
    JobQuestion = apps.get_model("jobs", "JobQuestion")
    JobComment = apps.get_model("jobs", "JobComment")
    questions = JobQuestion.objects.order_by("created_at", "id").values(
        "job_id",
        "asked_by__user_id",
        "body",
        "answer",
        "answered_by_id",
        "answered_at",
        "hidden_at",
        "created_at",
    )
    for question in questions.iterator():
        comment = JobComment.objects.create(
            job_id=question["job_id"],
            author_id=question["asked_by__user_id"],
            from_recruiter=True,
            body=question["body"],
            hidden_at=question["hidden_at"],
        )
        JobComment.objects.filter(pk=comment.pk).update(created_at=question["created_at"])
        if not question["answer"].strip():
            continue
        reply = JobComment.objects.create(
            job_id=question["job_id"],
            author_id=question["answered_by_id"],
            from_recruiter=False,
            parent_id=comment.pk,
            body=question["answer"],
        )
        JobComment.objects.filter(pk=reply.pk).update(
            created_at=question["answered_at"] or question["created_at"]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0012_invites_become_assignments"),
        ("recruiters", "0002_recruiter_review"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="JobComment",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("from_recruiter", models.BooleanField()),
                ("body", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("hidden_at", models.DateTimeField(blank=True, null=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "author",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "hidden_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="comments",
                        to="jobs.job",
                    ),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="replies",
                        to="jobs.jobcomment",
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at", "-id"),
                "indexes": [
                    models.Index(
                        fields=["job", "parent", "-created_at"],
                        name="jobs_jobcom_job_id_eb401c_idx",
                    )
                ],
            },
        ),
        migrations.RunPython(questions_to_comments, reverse_code=migrations.RunPython.noop),
        migrations.DeleteModel(
            name="JobQuestion",
        ),
    ]
