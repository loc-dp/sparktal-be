# sparktal-be

Django 5.2 API for the Sparktal platform.

## Dev — everything in Docker

```bash
cp .env.example .env
docker compose up -d --build        # postgres, redis, api, worker
docker compose exec api python manage.py migrate
docker compose exec api python manage.py seed_demo
```

The API answers on <http://localhost:8001> (8000 is taken by another project on
this machine; change `API_PORT` in `.env` if that stops being true). The Celery
worker runs in the same image and uses the default prefork pool — the container
is Linux, so the macOS fork problem does not apply.

Code is bind-mounted, so the dev server reloads on save. After changing
dependencies, rebuild: `docker compose up -d --build`.

Logs: `docker compose logs -f api worker`.
Shell: `docker compose exec api bash`, Django shell: `docker compose exec api python manage.py shell`.

## Dev — running Django on the host instead

The containers are optional; the host tooling talks to the same Postgres and
Redis. Stop the two app containers first so nothing else holds the port:

```bash
docker compose stop api worker
uv sync
uv run python manage.py runserver 8001
uv run celery -A config worker -l info --pool=solo   # macOS needs solo
```

On macOS the prefork pool forks before the app is finalised and every task dies
with `ValueError: not enough values to unpack (expected 3, got 0)`, hence
`--pool=solo` here but not in the container.

## Checks

Run on the host (fast, no container round-trip) or inside `api`:

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy .
uv run python manage.py spectacular --file openapi.yaml --validate
```

Demo password: `demo123` (users listed in `apps/accounts/seed.py`).
`seed_demo` refuses to run with `DEBUG=False` unless you pass `--force`.
