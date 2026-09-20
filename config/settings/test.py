import tempfile
from pathlib import Path

from .base import *  # noqa: F403
from .base import MIDDLEWARE

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="sparktal-test-media-"))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# Tests get a private in-process cache; no Redis needed and no cross-test bleed.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# No collected static files in tests; WhiteNoise would warn on every request.
MIDDLEWARE = [m for m in MIDDLEWARE if m != "whitenoise.middleware.WhiteNoiseMiddleware"]
