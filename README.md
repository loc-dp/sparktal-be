# sparktal-be

Django 5.2 API for the Sparktal platform.

## Dev

```bash
cp .env.example .env
docker compose up -d
uv sync
uv run python manage.py migrate
uv run python manage.py seed_demo
uv run python manage.py runserver 8001
```

The port is 8001 because another project on this machine already publishes 8000.
Change it here and in `sparktal-fe/.env.local` if that stops being true.

Demo password: `sparktal-demo-2026` (users listed in `apps/accounts/seed.py`).
`seed_demo` refuses to run with `DEBUG=False` unless you pass `--force`.

Background worker (macOS needs the solo pool: the prefork pool forks before the
app is finalised and every task dies with
`ValueError: not enough values to unpack (expected 3, got 0)`):

```bash
uv run celery -A config worker -l info --pool=solo
```

Checks: `uv run pytest`, `uv run ruff check .`, `uv run mypy .`.
Schema: `uv run python manage.py spectacular --file openapi.yaml --validate`.
