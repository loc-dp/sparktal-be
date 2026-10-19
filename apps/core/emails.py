from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template import Context
from django.template.loader import get_template, render_to_string


def email_branding() -> dict[str, str]:
    """Values every Sparktal email template expects."""
    return {"app_url": settings.FRONTEND_URL}


def build_email(template_prefix: str, to: str, context: dict[str, Any]) -> EmailMultiAlternatives:
    """Render `<prefix>_subject.txt`, `_message.txt` and `_message.html` into one email."""
    ctx = {**email_branding(), "email": to, **context}
    # A subject is plain text: "R&D Lead" must not arrive as "R&amp;D Lead".
    raw_subject = get_template(f"{template_prefix}_subject.txt").template.render(  # type: ignore[attr-defined]
        Context(ctx, autoescape=False)
    )
    subject = " ".join(raw_subject.splitlines()).strip()
    text = render_to_string(f"{template_prefix}_message.txt", ctx).strip()
    html = render_to_string(f"{template_prefix}_message.html", ctx).strip()
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to])
    message.attach_alternative(html, "text/html")
    return message
