from __future__ import annotations

from typing import Any

from django.core.files.uploadedfile import UploadedFile

from apps.core.documents import ALLOWED_DOCUMENT_EXTENSIONS, MAX_DOCUMENT_BYTES, validate_document

MAX_CV_BYTES = MAX_DOCUMENT_BYTES
ALLOWED_CV_EXTENSIONS = ALLOWED_DOCUMENT_EXTENSIONS
CV_TYPE_MESSAGE = "Only PDF, DOCX or DOC files can be uploaded as a CV."
CV_SIZE_MESSAGE = "CV files must be 10 MB or smaller."


def validate_cv(upload: UploadedFile[Any]) -> None:
    validate_document(upload, CV_TYPE_MESSAGE, CV_SIZE_MESSAGE)
