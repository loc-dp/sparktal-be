"""Uploaded documents read by Claude: CVs and job descriptions share this pipeline.

File checks, the text layer, the block Claude reads and the call itself live here;
each feature keeps its own output schema, prompt and validation gate.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
from anthropic.types import ContentBlockParam, OutputConfigParam
from django.core.exceptions import ValidationError
from django.core.files.base import File
from django.core.files.uploadedfile import UploadedFile
from pydantic import BaseModel

from apps.chat.attachments import MAX_ATTACHMENT_BYTES, validate_attachment

logger = logging.getLogger(__name__)

MAX_DOCUMENT_BYTES = MAX_ATTACHMENT_BYTES
ALLOWED_DOCUMENT_EXTENSIONS = (".pdf", ".docx", ".doc")
DOC_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def validate_document(upload: UploadedFile[Any], type_message: str, size_message: str) -> None:
    """Accept PDF, DOCX and legacy DOC up to 10 MB, with content matching the extension."""
    extension = Path(upload.name or "").suffix.lower()
    if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise ValidationError(type_message)
    if (upload.size or 0) > MAX_DOCUMENT_BYTES:
        raise ValidationError(size_message)
    if extension != ".doc":
        validate_attachment(upload)
        return
    upload.seek(0)
    try:
        if upload.read(len(DOC_MAGIC)) != DOC_MAGIC:
            raise ValidationError("This file does not look like a real DOC.")
    finally:
        upload.seek(0)


def _pdf_text(upload: File[Any]) -> str:
    from pypdf import PdfReader

    reader = PdfReader(upload)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _docx_text(upload: File[Any]) -> str:
    from docx import Document

    document = Document(upload)
    lines = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.extend(cell.text for cell in row.cells)
    return "\n".join(lines)


def extract_text(file: File[Any]) -> str:
    extension = Path(file.name or "").suffix.lower()
    reader = {".pdf": _pdf_text, ".docx": _docx_text}.get(extension)
    if reader is None:
        return ""
    file.seek(0)
    try:
        return reader(file).strip()
    except Exception:
        return ""
    finally:
        file.seek(0)


SCANNED_TEXT_THRESHOLD = 80


def is_scanned(filename: str, text: str) -> bool:
    """A PDF with almost no text layer is a picture of a document."""
    return Path(filename).suffix.lower() == ".pdf" and len(text.strip()) < SCANNED_TEXT_THRESHOLD


@dataclass(frozen=True)
class ClaudeRead:
    """What one call returned and cost. fields is None when the reply was unusable."""

    fields: dict[str, Any] | None
    model: str
    input_tokens: int
    output_tokens: int


def anthropic_client(api_key: str, timeout: float) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=1)


PDF_HEADER = b"%PDF-"
# Readers accept junk before the header within the first 1024 bytes; Claude does not.
PDF_HEADER_WINDOW = 1024


def source_block(data: bytes, filename: str, text: str, tag: str) -> ContentBlockParam | None:
    """A PDF goes to Claude as itself, so layout and scans survive; anything else as text."""
    if Path(filename).suffix.lower() == ".pdf":
        start = data.find(PDF_HEADER, 0, PDF_HEADER_WINDOW)
        if start > 0:
            data = data[start:]
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(data).decode("ascii"),
            },
        }
    if text:
        return {"type": "text", "text": f"<{tag}>\n{text}\n</{tag}>"}
    return None


def read_with_claude(
    client: anthropic.Anthropic,
    *,
    model: str,
    source: ContentBlockParam,
    instructions: str,
    output_format: type[BaseModel],
    max_tokens: int,
    output_config: OutputConfigParam,
    label: str,
) -> ClaudeRead | None:
    """Ask Claude for `output_format`; None when no reply came back at all.

    Logs never quote the reply: it holds personal or company data. `label` names the
    document in log lines, e.g. "CV".
    """
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            output_config=output_config,
            messages=[
                {"role": "user", "content": [source, {"type": "text", "text": instructions}]}
            ],
            output_format=output_format,
        )
    except anthropic.APIError:
        logger.warning("Claude %s parse failed", label, exc_info=True)
        return None
    except ValueError as error:
        # A pydantic error quotes the raw reply: log its type only.
        logger.warning("Claude %s parse returned unusable output (%s)", label, type(error).__name__)
        return None
    fields = None
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        logger.warning(
            "Claude %s parse stopped with %s (request %s)",
            label,
            response.stop_reason,
            response._request_id,
        )
    else:
        fields = response.parsed_output.model_dump()
    return ClaudeRead(
        fields=fields,
        model=response.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
