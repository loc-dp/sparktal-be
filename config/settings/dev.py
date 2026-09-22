from .base import *  # noqa: F403
from .base import ACCOUNT_RATE_LIMITS, MIDDLEWARE, env

DEBUG = env.bool("DJANGO_DEBUG", default=True)

ACCOUNT_RATE_LIMITS = {**ACCOUNT_RATE_LIMITS, "login": None}

MIDDLEWARE = [m for m in MIDDLEWARE if m != "whitenoise.middleware.WhiteNoiseMiddleware"]
