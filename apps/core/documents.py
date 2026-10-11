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


# A table cell this short is a label ("Tên chức danh / Position Title"), kept on one line.
DOCX_LABEL_LENGTH = 60
DOCX_BULLETS = ("•", "●", "○", "◦", "▪", "■", "►", "-", "–", "*", "+", "✓", "✔", "➢")


def _docx_list_level(paragraph: Any) -> int | None:
    """How deep in a bulleted or numbered list a paragraph sits; None outside a list."""
    p_pr = paragraph._p.pPr
    num_pr = p_pr.numPr if p_pr is not None else None
    if num_pr is None:
        style = paragraph.style.name if paragraph.style is not None else ""
        return 0 if style.startswith("List") else None
    if num_pr.numId is not None and num_pr.numId.val == 0:
        return None
    return num_pr.ilvl.val if num_pr.ilvl is not None else 0


def _docx_text_boxes(paragraph: Any) -> list[str]:
    from docx.oxml.ns import qn

    lines: list[str] = []
    # A text box is stored twice (drawing and VML fallback); keep the first copy.
    for box in paragraph._p.iter(qn("w:txbxContent")):
        for p in box.iter(qn("w:p")):
            line = " ".join("".join(t.text or "" for t in p.iter(qn("w:t"))).split())
            if line and line not in lines:
                lines.append(line)
    return lines


def _docx_paragraph(paragraph: Any) -> list[str]:
    lines = [" ".join(part.split()) for part in paragraph.text.splitlines()]
    lines = [line for line in lines if line]
    level = _docx_list_level(paragraph)
    if lines and level is not None and not lines[0].startswith(DOCX_BULLETS):
        indent = "  " * level
        lines = [f"{indent}- {lines[0]}", *(f"{indent}  {line}" for line in lines[1:])]
    return lines + _docx_text_boxes(paragraph)


def _docx_cell(lines: list[str]) -> list[str]:
    """A short bilingual label cell reads as one line; a cell of bullets keeps its lines."""
    if 1 < len(lines) <= 3 and all(
        len(line) <= DOCX_LABEL_LENGTH and not line.lstrip().startswith(DOCX_BULLETS)
        for line in lines
    ):
        return [" / ".join(lines)]
    return lines


def _docx_table(table: Any) -> list[str]:
    """Row by row, cells side by side; a merged cell is read once."""
    from docx.table import _Cell

    lines: list[str] = []
    for tr in table._tbl.tr_lst:
        row: list[str] = []
        for tc in tr.tc_lst:
            if tc.vMerge == "continue":
                continue
            cell = _docx_cell(_docx_blocks(tc, _Cell(tc, table)))
            if len(cell) == 1:
                row.append(cell[0])
                continue
            if row:
                lines.append(" | ".join(row))
                row = []
            lines.extend(cell)
        if row:
            lines.append(" | ".join(row))
    return lines


def _docx_blocks(element: Any, parent: Any) -> list[str]:
    """Paragraphs and tables in reading order, including those inside content controls."""
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    lines: list[str] = []
    for child in element.iterchildren():
        if child.tag == qn("w:p"):
            lines.extend(_docx_paragraph(Paragraph(child, parent)))
        elif child.tag == qn("w:tbl"):
            lines.extend(_docx_table(Table(child, parent)))
        elif child.tag == qn("w:sdt"):
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                lines.extend(_docx_blocks(content, parent))
    return lines


def _docx_text(upload: File[Any]) -> str:
    """The document as Claude and people should read it: in order, lists and tables kept.

    The first page header comes first: forms often put the title or company there.
    """
    from docx import Document

    document = Document(upload)
    lines: list[str] = []
    header = document.sections[0].header if document.sections else None
    if header is not None and not header.is_linked_to_previous:
        lines.extend(_docx_blocks(header._element, header))
    lines.extend(_docx_blocks(document.element.body, document))
    deduped = [line for index, line in enumerate(lines) if index == 0 or line != lines[index - 1]]
    return "\n".join(deduped)


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
