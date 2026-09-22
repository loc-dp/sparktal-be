from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, UnidentifiedImageError

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = (".pdf", ".docx", ".png", ".jpg", ".jpeg")
TYPE_MESSAGE = "Only PDF, DOCX, PNG or JPG files can be attached."
SIZE_MESSAGE = "Attachments must be 10 MB or smaller."
PDF_MAGIC = b"%PDF-"
DOCX_ENTRY = "word/document.xml"
IMAGE_FORMATS = {".png": {"PNG"}, ".jpg": {"JPEG"}, ".jpeg": {"JPEG"}}


def _mismatch(extension: str) -> ValidationError:
    kind = extension.lstrip(".").upper()
    return ValidationError(f"This file does not look like a real {kind}.")


def _check_pdf(upload: UploadedFile[Any]) -> None:
    if upload.read(len(PDF_MAGIC)) != PDF_MAGIC:
        raise _mismatch(".pdf")


def _check_docx(upload: UploadedFile[Any]) -> None:
    try:
        with zipfile.ZipFile(upload) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile as error:
        raise _mismatch(".docx") from error
    if DOCX_ENTRY not in names:
        raise _mismatch(".docx")


def _check_image(upload: UploadedFile[Any], extension: str) -> None:
    try:
        image = Image.open(upload)
        image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise _mismatch(extension) from error
    if image.format not in IMAGE_FORMATS[extension]:
        raise _mismatch(extension)


def validate_attachment(upload: UploadedFile[Any]) -> None:
    extension = Path(upload.name or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError(TYPE_MESSAGE)
    if (upload.size or 0) > MAX_ATTACHMENT_BYTES:
        raise ValidationError(SIZE_MESSAGE)

    upload.seek(0)
    try:
        if extension == ".pdf":
            _check_pdf(upload)
        elif extension == ".docx":
            _check_docx(upload)
        else:
            _check_image(upload, extension)
    finally:
        upload.seek(0)
