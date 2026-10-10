import logging
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.utils import timezone

from apps.core.documents import ClaudeRead, extract_text
from apps.jobs.jd_fields import OfficeRef, has_content, normalize_jd
from apps.jobs.jd_parsing import llm_extract
from apps.jobs.models import JdParse

logger = logging.getLogger(__name__)
UNREADABLE = "We couldn't read that job description. Fill the form in yourself."
OLD_DOC = "We can't read old .doc files. Save the job description as PDF and upload it again."
NOT_A_JD = "That file doesn't look like a job description. Fill the form in yourself."
# A parse only prefills the form on screen; after a day its file is just stored company data.
JD_PARSE_TTL = timedelta(hours=24)


def record_usage(attempt: JdParse, claude: ClaudeRead | None) -> None:
    if claude is not None:
        attempt.record_llm_usage(claude.model, claude.input_tokens, claude.output_tokens)


def offices_of(attempt: JdParse) -> list[OfficeRef]:
    return [
        OfficeRef(id=str(office.pk), city=office.city) for office in attempt.company.offices.all()
    ]


@shared_task(name="jobs.parse_jd")
def parse_jd(parse_id: str) -> None:
    attempt = JdParse.objects.select_related("company").filter(pk=parse_id).first()
    if attempt is None:
        logger.warning("JD parse %s no longer exists", parse_id)
        return
    attempt.mark_running()
    try:
        with attempt.file.open("rb") as handle:
            text = extract_text(handle)
            data = handle.read()
        claude = llm_extract(data, attempt.filename, text)
        record_usage(attempt, claude)
        if claude is None or claude.fields is None:
            old_doc = Path(attempt.filename).suffix.lower() == ".doc"
            attempt.mark_failed(OLD_DOC if old_doc else UNREADABLE)
            return
        fields = normalize_jd(claude.fields, offices_of(attempt))
        if not has_content(fields):
            attempt.mark_failed(NOT_A_JD)
            return
        attempt.mark_done(fields)
    except Exception:
        logger.exception("JD parse %s failed", parse_id)
        attempt.mark_failed(UNREADABLE)


@shared_task(name="jobs.purge_stale_jd_parses")
def purge_stale_jd_parses() -> int:
    """Delete parses older than JD_PARSE_TTL, whatever their status, with their files."""
    removed = 0
    for attempt in JdParse.objects.filter(created_at__lt=timezone.now() - JD_PARSE_TTL).iterator():
        if attempt.file:
            attempt.file.delete(save=False)
        attempt.delete()
        removed += 1
    return removed
