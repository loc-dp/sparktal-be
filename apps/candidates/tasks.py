import logging
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.utils import timezone

from apps.candidates.cv_fields import fill_contacts, normalize_fields
from apps.candidates.llm_parsing import ClaudeRead, llm_extract
from apps.candidates.models import CvParse, CvParseReader
from apps.candidates.parsing import extract_text, guess_fields, is_scanned

logger = logging.getLogger(__name__)
UNREADABLE = "We couldn't read that CV. Fill the form in yourself."
# A parse only prefills the form on screen; after a day its file and fields are just stored PII.
CV_PARSE_TTL = timedelta(hours=24)


class UnreadableCvError(Exception):
    """Neither Claude nor a text layer could read the file."""

    def __init__(self, filename: str, claude: ClaudeRead | None) -> None:
        super().__init__(filename)
        self.claude = claude


def read_fields(data: bytes, filename: str, text: str) -> tuple[dict[str, Any], ClaudeRead | None]:
    """Claude first; regex fills the gaps. Every value goes through one gate."""
    scanned = is_scanned(filename, text)
    # A scan's stray text layer ("Scanned with CamScanner") is neither a source nor ground truth.
    source_text = "" if scanned else text
    claude = llm_extract(data, filename, text)
    llm = claude.fields if claude else None
    if llm is None and not source_text.strip():
        raise UnreadableCvError(filename, claude)
    regex = normalize_fields(guess_fields(source_text), source_text)
    fields = fill_contacts(normalize_fields(llm, source_text), regex) if llm else regex
    fields["source"] = "scanned" if scanned else "text"
    return fields, claude


def record_usage(attempt: CvParse, claude: ClaudeRead | None) -> None:
    if claude is not None:
        attempt.record_llm_usage(claude.model, claude.input_tokens, claude.output_tokens)


@shared_task(name="candidates.parse_cv")
def parse_cv(parse_id: str) -> None:
    attempt = CvParse.objects.filter(pk=parse_id).first()
    if attempt is None:
        logger.warning("CV parse %s no longer exists", parse_id)
        return
    attempt.mark_running()
    try:
        with attempt.file.open("rb") as handle:
            text = extract_text(handle)
            data = handle.read()
        fields, claude = read_fields(data, attempt.filename, text)
        record_usage(attempt, claude)
        reader = CvParseReader.CLAUDE if claude and claude.fields else CvParseReader.RULES
        attempt.mark_done(fields, reader)
    except UnreadableCvError as error:
        logger.info("CV parse %s found nothing to read", parse_id)
        record_usage(attempt, error.claude)
        attempt.mark_failed(UNREADABLE)
    except Exception:
        logger.exception("CV parse %s failed", parse_id)
        attempt.mark_failed(UNREADABLE)


@shared_task(name="candidates.purge_stale_cv_parses")
def purge_stale_cv_parses() -> int:
    """Delete parses older than CV_PARSE_TTL, whatever their status, with their files."""
    removed = 0
    for attempt in CvParse.objects.filter(created_at__lt=timezone.now() - CV_PARSE_TTL).iterator():
        if attempt.file:
            attempt.file.delete(save=False)
        attempt.delete()
        removed += 1
    return removed
