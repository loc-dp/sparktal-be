import logging

from celery import shared_task

from apps.candidates.models import CvParse
from apps.candidates.parsing import extract_text, guess_fields

logger = logging.getLogger(__name__)
UNREADABLE = "We couldn't read that CV. Fill the form in yourself."


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
        attempt.mark_done(guess_fields(text))
    except Exception:
        logger.exception("CV parse %s failed", parse_id)
        attempt.mark_failed(UNREADABLE)
