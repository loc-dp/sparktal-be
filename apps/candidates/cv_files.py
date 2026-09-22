from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile

from apps.chat.attachments import MAX_ATTACHMENT_BYTES, validate_attachment

MAX_CV_BYTES = MAX_ATTACHMENT_BYTES
ALLOWED_CV_EXTENSIONS = (".pdf", ".docx", ".doc")
CV_TYPE_MESSAGE = "Only PDF, DOCX or DOC files can be uploaded as a CV."
CV_SIZE_MESSAGE = "CV files must be 10 MB or smaller."
DOC_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def validate_cv(upload: UploadedFile[Any]) -> None:
    extension = Path(upload.name or "").suffix.lower()
    if extension not in ALLOWED_CV_EXTENSIONS:
        raise ValidationError(CV_TYPE_MESSAGE)
    if (upload.size or 0) > MAX_CV_BYTES:
        raise ValidationError(CV_SIZE_MESSAGE)
    if extension != ".doc":
        validate_attachment(upload)
        return
    upload.seek(0)
    try:
        if upload.read(len(DOC_MAGIC)) != DOC_MAGIC:
            raise ValidationError("This file does not look like a real DOC.")
    finally:
        upload.seek(0)
