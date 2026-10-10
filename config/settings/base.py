"""Settings shared by every environment. Values come from the environment / `.env`."""

from pathlib import Path
from typing import Any

import environ
from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

INSECURE_DEV_SECRET_KEY = "django-insecure-dev-only-change-me"

SECRET_KEY = env.str("DJANGO_SECRET_KEY", default=INSECURE_DEV_SECRET_KEY)
DEBUG = env.bool("DJANGO_DEBUG", default=False)

if not DEBUG and SECRET_KEY == INSECURE_DEV_SECRET_KEY:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set when DEBUG is off. The built-in fallback is "
        "for local development only."
    )
ALLOWED_HOSTS: list[str] = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "corsheaders",
    "django_filters",
    "guardian",
    "rules.apps.AutodiscoverRulesConfig",
    "simple_history",
    "phonenumber_field",
    "allauth",
    "allauth.account",
    "allauth.headless",
    "apps.core",
    "apps.accounts",
    "apps.companies",
    "apps.recruiters",
    "apps.jobs",
    "apps.candidates",
    "apps.exports",
    "apps.chat",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": env.db(
        "DATABASE_URL", default="postgres://sparktal:sparktal@localhost:5432/sparktal"
    ),
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "rules.permissions.ObjectPermissionBackend",
    "guardian.backends.ObjectPermissionBackend",
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

ANONYMOUS_USER_NAME = None

ACCOUNT_ADAPTER = "apps.accounts.adapter.AccountAdapter"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_VERIFICATION = "optional"
ACCOUNT_PREVENT_ENUMERATION = True
ACCOUNT_PASSWORD_RESET_TOKEN_EXPIRY = 3 * 24 * 60 * 60  # 3 days for the invite email
ACCOUNT_RATE_LIMITS: dict[str, str | None] = {
    "login": "120/m/ip",
    "login_failed": "30/5m/ip,5/5m/key",
    "reset_password": "3/10m/key",
    "reset_password_from_key": "10/10m/ip",
}

LANGUAGE_CODE = "en"
TIME_ZONE = "Asia/Ho_Chi_Minh"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

USE_S3 = env.bool("USE_S3", default=False)
STORAGES: dict[str, dict[str, Any]] = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
if USE_S3:
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env.str("AWS_STORAGE_BUCKET_NAME"),
            "access_key": env.str("AWS_ACCESS_KEY_ID"),
            "secret_key": env.str("AWS_SECRET_ACCESS_KEY"),
            "endpoint_url": env.str("AWS_S3_ENDPOINT_URL", default=None),
            "region_name": env.str("AWS_S3_REGION_NAME", default="auto"),
            "default_acl": None,
            "querystring_auth": True,
            "file_overwrite": False,
        },
    }

REDIS_URL = env.str("REDIS_URL", default="redis://localhost:6379/0")

CHANNEL_LAYERS: dict[str, dict[str, Any]] = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    },
}

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env.str("CACHE_URL", default=REDIS_URL.rsplit("/", 1)[0] + "/1"),
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        "KEY_PREFIX": "sparktal",
    }
}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = env.str("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_BEAT_SCHEDULE = {
    "purge-expired-cv-exports": {
        "task": "exports.purge_expired_cv_exports",
        "schedule": crontab(minute=7),
    },
    "purge-stale-cv-parses": {
        "task": "candidates.purge_stale_cv_parses",
        "schedule": crontab(minute=37),
    },
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default="Sparktal <no-reply@sparktal.com>")

# CV parsing with Claude. Both must be set; otherwise only the regex parser runs.
ANTHROPIC_API_KEY = env.str("ANTHROPIC_API_KEY", default="")
CV_PARSE_MODEL = env.str("CV_PARSE_MODEL", default="")
CV_PARSE_TIMEOUT_SECONDS = env.float("CV_PARSE_TIMEOUT_SECONDS", default=30.0)

FRONTEND_URL = env.str("FRONTEND_URL", default="http://localhost:3000")

HEADLESS_ONLY = True
HEADLESS_FRONTEND_URLS = {
    "account_reset_password": f"{FRONTEND_URL}/forgot-password",
    "account_reset_password_from_key": f"{FRONTEND_URL}/reset-password?key={{key}}",
    "account_signup": f"{FRONTEND_URL}/sign-in",
}
HEADLESS_SERVE_SPECIFICATION = False
FRONTEND_ORIGINS: list[str] = env.list("FRONTEND_ORIGINS", default=["http://localhost:3000"])
CORS_ALLOWED_ORIGINS = FRONTEND_ORIGINS
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = FRONTEND_ORIGINS

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.DefaultPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"signup": "5/hour", "nickname": "30/min", "cv_parse": "60/hour"},
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Sparktal API",
    "DESCRIPTION": "Sparktal Platform V.1 — Company portal API.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "ENUM_NAME_OVERRIDES": {
        "LocationEnum": "apps.companies.models.Location.choices",
        "CvExportStatusEnum": "apps.exports.models.CvExportStatus.choices",
        "JobStatusEnum": "apps.jobs.models.JobStatus.choices",
    },
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
