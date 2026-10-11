from datetime import datetime
from typing import TypedDict

from apps.candidates.models import Application
from apps.chat.serializers import display_name


class StageEntry(TypedDict):
    stage: str
    at: datetime
    by: str | None


def stage_history(application: Application) -> list[StageEntry]:
    """Each stage the application entered, oldest first, and who moved it there.

    Read from simple_history; saves that kept the stage (edits to notes, the CV) are skipped.
    """
    records = application.history.order_by("history_date", "history_id").select_related(
        "history_user"
    )
    entries: list[StageEntry] = []
    for record in records:
        if entries and entries[-1]["stage"] == record.stage:
            continue
        user = record.history_user
        entries.append(
            {
                "stage": record.stage,
                "at": record.stage_changed_at,
                "by": display_name(user) if user is not None else None,
            }
        )
    return entries
