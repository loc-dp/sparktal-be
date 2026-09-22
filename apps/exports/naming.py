"""Safe, human-readable entry names for files inside a CV export ZIP."""

import re
from collections.abc import Iterable
from pathlib import PurePosixPath

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_SPACES = re.compile(r"\s+")


def sanitise(value: str) -> str:
    cleaned = _UNSAFE.sub(" ", value or "")
    while ".." in cleaned:
        cleaned = cleaned.replace("..", ".")
    cleaned = _SPACES.sub(" ", cleaned).strip(" .")
    return cleaned or "Unknown"


def extension_of(filename: str) -> str:
    suffix = PurePosixPath(filename or "").suffix.lower().lstrip(".")
    return re.sub(r"[^a-z0-9]", "", suffix) or "pdf"


def entry_name(position: int, candidate_name: str, nickname: str, source_filename: str) -> str:
    return (
        f"{position:02d} - {sanitise(candidate_name)} - {sanitise(nickname)}"
        f".{extension_of(source_filename)}"
    )


def unique_entry_names(applications: Iterable[object]) -> list[str]:
    """Build deterministic unique names; the position prefix normally provides uniqueness."""
    names: list[str] = []
    seen: dict[str, int] = {}
    for position, application in enumerate(applications, start=1):
        name = entry_name(
            position,
            application.candidate.full_name,  # type: ignore[attr-defined]
            application.recruiter.nickname,  # type: ignore[attr-defined]
            application.cv_filename,  # type: ignore[attr-defined]
        )
        key = name.casefold()
        count = seen.get(key, 0) + 1
        seen[key] = count
        if count > 1:
            stem, _, extension = name.rpartition(".")
            name = f"{stem} ({count}).{extension}"
        names.append(name)
    return names
