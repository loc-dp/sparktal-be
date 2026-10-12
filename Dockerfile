# One Dockerfile for every environment. Pick the stage with `target`:
#   dev   the locked dependencies including the dev group; the source is bind-mounted
#         (compose.yaml).
#   prod  the default and last stage: runtime dependencies only, the source baked in, the
#         static files collected, no root. One image runs the API, the Celery worker and beat
#         (compose.prod.yaml picks the command).

FROM python:3.13-slim-trixie AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    # The venv lives outside /app so a bind mount never shadows it and the macOS host venv
    # (different platform) is never reused inside the container.
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app


# The tool stage: uv is needed to install dependencies, not to run the app.
FROM base AS tools

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./


FROM tools AS dev

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-install-project

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]


FROM tools AS prod-deps

# The dev group (pytest, ruff, daphne, …) stays out of the image.
RUN uv sync --frozen --no-dev --no-install-project


FROM base AS prod

ENV DJANGO_SETTINGS_MODULE=config.settings.prod

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app

COPY --from=prod-deps /opt/venv /opt/venv
COPY --chown=app:app . .

# collectstatic only reads the settings, so throwaway values stand in for the real ones,
# which exist only at run time.
RUN DJANGO_SECRET_KEY=build-only EMAIL_HOST=build-only USE_S3=True \
    AWS_STORAGE_BUCKET_NAME=build-only AWS_ACCESS_KEY_ID=build-only \
    AWS_SECRET_ACCESS_KEY=build-only \
    python manage.py collectstatic --noinput

USER app

EXPOSE 8000

# UvicornWorker keeps the chat WebSockets working under gunicorn. WEB_CONCURRENCY sets the
# number of workers, and FORWARDED_ALLOW_IPS lets the reverse proxy name the client's address.
CMD ["gunicorn", "config.asgi:application", \
     "--worker-class", "uvicorn_worker.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--access-logfile", "-"]
