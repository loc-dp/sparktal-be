from .base import *  # noqa: F403
from .base import MIDDLEWARE, env

DEBUG = env.bool("DJANGO_DEBUG", default=True)

# runserver (daphne) serves static files itself in DEBUG; WhiteNoise is for production.
MIDDLEWARE = [m for m in MIDDLEWARE if m != "whitenoise.middleware.WhiteNoiseMiddleware"]
