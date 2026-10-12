"""Production settings. Every secret and host name comes from the environment."""

import sentry_sdk
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import INSTALLED_APPS, STORAGES, USE_S3, env

DEBUG = False

# daphne only swaps in its own runserver during development, and it is a dev dependency.
INSTALLED_APPS = [app for app in INSTALLED_APPS if app != "daphne"]

# Uploads (CVs, logos, exports) must outlive a container, and nothing in production serves
# MEDIA_ROOT. S3-compatible storage, such as Cloudflare R2, is the only supported home.
if not USE_S3:
    raise ImproperlyConfigured(
        "Production stores uploads in S3: set USE_S3=True and the AWS_* keys."
    )

# collectstatic fingerprints the files and WhiteNoise serves them, with compression.
STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# TLS ends at the reverse proxy, which must send X-Forwarded-Proto and strip any copy a client sent.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Off by default so a health check inside the container (plain HTTP) is not redirected.
# The proxy redirects HTTP to HTTPS; turn this on if it does not.
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31_536_000)
SECURE_CONTENT_TYPE_NOSNIFF = True

# The app and the API sit on sibling hosts (app.example.com, api.example.com). The browser
# reads the csrftoken cookie on the app host and the app's server forwards the session cookie
# to the API, so both cookies must be set on the parent domain, e.g. ".example.com".
SESSION_COOKIE_DOMAIN: str | None = env.str("COOKIE_DOMAIN", default=None)
CSRF_COOKIE_DOMAIN: str | None = SESSION_COOKIE_DOMAIN
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env.str("EMAIL_HOST")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env.str("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env.str("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)

if dsn := env.str("SENTRY_DSN", default=""):
    sentry_sdk.init(
        dsn=dsn,
        environment=env.str("SENTRY_ENVIRONMENT", default="production"),
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0),
        send_default_pii=False,
    )
