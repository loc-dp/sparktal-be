from pathlib import Path

from django.db import models


class CvKind(models.TextChoices):
    PDF = "pdf", "PDF"
    DOCX = "docx", "Word (DOCX)"
    DOC = "doc", "Word 97–2003 (DOC)"
    OTHER = "other", "Other"


def cv_kind(filename: str) -> CvKind:
    """How the viewer can show a CV.

    Render a PDF, show a DOCX as text, offer the rest as a download.
    """
    suffix = Path(filename or "").suffix.lower().lstrip(".")
    return CvKind(suffix) if suffix in CvKind.values and suffix != CvKind.OTHER else CvKind.OTHER
