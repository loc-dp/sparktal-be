"""Django leaves a FileField's blob in storage when its row is deleted; this removes it."""

from __future__ import annotations

from functools import partial
from typing import Any

from django.db import models, transaction
from django.db.models.signals import post_delete


def delete_files_with_rows(model: type[models.Model], *fields: str) -> None:
    """Delete these files' blobs once a row's delete commits, cascades included.

    The blob goes only after commit, so a rolled-back delete never loses a file its row
    still points to.
    """

    def remove_blobs(sender: type[models.Model], instance: models.Model, **kwargs: Any) -> None:
        for field in fields:
            file = getattr(instance, field)
            if file:
                transaction.on_commit(
                    partial(file.storage.delete, file.name), using=kwargs["using"]
                )

    post_delete.connect(
        remove_blobs,
        sender=model,
        weak=False,
        dispatch_uid=f"core.delete_files_with_rows.{model._meta.label}",
    )
