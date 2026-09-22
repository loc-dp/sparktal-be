"""HTTP helpers shared by file-streaming endpoints."""

import re
import unicodedata
from urllib.parse import quote

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _safe_filename(filename: str) -> str:
    cleaned = _UNSAFE.sub("", filename).replace("..", "").strip()
    return cleaned or "download"


def ascii_fallback(filename: str) -> str:
    """Return a plain-ASCII name for clients that ignore RFC 5987."""
    safe = _safe_filename(filename)
    folded = unicodedata.normalize("NFKD", safe).encode("ascii", "ignore").decode("ascii")
    return folded or "download"


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """Build a safe RFC 6266/RFC 5987 Content-Disposition value."""
    disposition = "inline" if inline else "attachment"
    safe = _safe_filename(filename)
    fallback = ascii_fallback(safe)
    encoded = quote(safe, safe="")
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"
