# Gotchas — sparktal-be

Traps this codebase has already hit, with the shape of the failure and what the
code does about it. Each was found by running the thing, not by reading docs.
Check here before "fixing" something that looks odd.

## django-stubs generics are type-checker only

`admin.ModelAdmin[Model]`, `admin.TabularInline[Model]` and `UploadedFile[Any]`
raise `TypeError: type 'X' is not subscriptable` at import time. The classes stay
bare with `# type: ignore[type-arg]`; `apps/companies/api.py` uses
`from __future__ import annotations` so its annotations are never evaluated.

## drf-spectacular rejects a class-level operation_id

`@extend_schema(operation_id=...)` on a generic view logs *"using @extend_schema
on viewset class … will most likely result in a broken schema"* and
`spectacular --fail-on-warn` fails. Put it on the method with
`@extend_schema_view(get=extend_schema(...))`.

`SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]` names `Location`, which
`Company.located` and `Office.city` share; without it the generator warns about
two names for one choice set.

## Uploads must be inline multipart, or the typed client breaks

A `$ref`'d binary schema comes out of orval as `file: unknown`, and a view that
also accepts `FormParser` advertises `application/x-www-form-urlencoded`, which
has the same effect. Upload views therefore declare the body inline in
`@extend_schema` and accept `MultiPartParser` only.

Chat is the exception to the field name assumed by the original frontend
post-processor: its binary field is named `attachment`, not `file`. Keep the
OpenAPI request explicitly on `multipart/form-data`, and keep
`scripts/fix-generated-uploads.mjs` aware of both names; otherwise Orval emits
JSON or calls `FormData.append()` with `unknown`.

## Chat has one write path and one delivery path

Messages are persisted only by the REST endpoint. The endpoint schedules the
WebSocket broadcast with `transaction.on_commit()`, so receivers never react to
a row that can still roll back. The consumer is push-only: it accepts `ping`,
ignores every other client frame, and joins only `user_<uuid hex>`.

DRF serializer data is not automatically safe for the stdlib JSON encoder:
UUID values can remain Python `UUID` objects. Chat events pass serializer output
through `JSONRenderer` before `group_send`; sending `MessageSerializer(...).data`
directly crashes the consumer with `UUID is not JSON serializable`.

Use `OriginValidator` with complete entries from `FRONTEND_ORIGINS`. An
`AllowedHostsOriginValidator` pattern without a scheme ignores port and compares
against backend hosts, which is both too loose in development and wrong when the
production frontend uses another domain.

## Chat access and identity are server-owned

Every channel lookup returns 404 when `can_access_channel()` fails so channel
IDs cannot be probed. Company-side visibility comes from `jobs_visible_to()`;
recruiters see only channels bound to their own profile. Recruiter authors are
serialized through `display_name()` as the profile nickname—never full name or
email—and message bodies are stored as plain text after `nh3` removes markup.

Unread counts exclude the current user's messages but include system messages.
Read markers only move forward; accepting an older timestamp can resurrect
already-cleared unread counts.

## Chat seed data depends on signal order

Application creation opens `(job, recruiter)` channels and adds system messages.
The order-70 chat seeder adds only human conversation, back-dates rows with a
queryset update because `created_at` is `auto_now_add`, then recomputes
`last_message_at`. Its order-70 resetter must delete channels before candidate
reset/reseed, or repeated `seed_demo --reset` runs accumulate system messages.

## DRF field validation runs before `validate_<field>`

`URLField` answers *"Enter a valid URL."*, `CharField` answers *"This field may
not be blank."* and `ImageField` answers *"not an image"* before any custom
validator sees the value — so the UI's own wording never appears. Fields whose
message comes from the prototype are declared as plain `CharField`/`FileField`
and validated by hand (`apps/companies/serializers.py`, `apps/jobs/serializers.py`).

Model validators do the same: `bounty_pct` is redeclared on the serializer
without the model's range validators so `validate()` can answer in the studio's
words.

## Permission checks must run before validation

A recruiter posting to `/api/v1/jobs` got a 400 about fields instead of a 403,
because the serializer validated first. Job creation uses the `CanAddJob`
permission class; anything similar should too.

## allauth behaves differently from the obvious guess

- Inactive account on login: **401**, not 400.
- Password reset for an unknown address: still sends mail — the *"Unknown
  Account"* notice with no reset key (`ACCOUNT_PREVENT_ENUMERATION`).
- Too many failed logins: **400** with `code: "too_many_login_attempts"`, not 429.
  Only actions wrapped in allauth's `rate_limit` decorator return 429.
- `ACCOUNT_RATE_LIMITS["login_failed"]` keeps the per-account budget tight
  (5/5m) but the per-IP one loose (30/5m): a whole company shares one NAT
  address, so a tight IP budget lets one person lock out the office.
- allauth also rate-limits **successful** logins, `login: 30/m/ip` by default.
  That is below what one office behind a NAT does at 9am, and it broke the E2E
  suite the moment it passed 30 sign-ins in a minute: allauth answered `429
  {"status": 429}` and the UI showed *"Too many attempts. Wait a few minutes and
  try again."* — which reads as a broken login, not a limit. `base.py` raises it
  to `120/m/ip` and `dev.py` sets it to `None` so the suite never trips it.
- `ResetPasswordForm.save(request)` needs the request pushed into
  `allauth.core.context.request_context`, or it raises `'NoneType' object has no
  attribute 'get_host'`. `RequestFactory` also defaults to host `testserver`,
  which `ALLOWED_HOSTS` rejects outside tests — `create_company` borrows the
  first allowed host.

## The cache must be Redis, not LocMem

allauth counts login and reset attempts in the Django cache. With the default
LocMem backend those counters live in one process, so the E2E suite's
`manage.py shell -c "cache.clear()"` cleared a *different* cache and the suite
failed at random. `CACHES` points at Redis (`CACHE_URL`, db 1); tests override it
with LocMem for isolation.

## Recruiter signup and review boundaries

Python regex `\w` includes `_`, although marketplace nicknames do not allow it.
The backend nickname expression explicitly excludes underscores to match the
frontend schema. A lowercase project name such as `sparktal` is not necessarily
rejected by Django's default password validators; use a genuinely common or
short password when testing `validate_password()`.

`ScopedRateThrottle` applies only to views with `throttle_scope`: recruiter
signup is 5/hour/IP and nickname-check is 30/min/IP, while existing endpoints
keep their behavior. Review fields remain read-only in the profile serializer;
only Django admin actions call `approve()` or `reject()`. The approval email
links to `/r/chat` until Plan 10 creates `/r/home`.

The recruiter resetter deletes `@e2e.example.com` signups before reseeding.
Without it, repeated identity E2E runs leave extra pending accounts behind.

## Celery

- On the macOS host the prefork pool forks before the app is finalised and every
  task dies with `ValueError: not enough values to unpack (expected 3, got 0)` —
  run `--pool=solo` there. Inside the Linux container the default pool is fine.
- A result backend is configured so `.delay().get()` works for smoke checks; long
  jobs still track their own status row.

## Seeding is a contract, not a convenience

`seed_demo` must leave the database in exactly one state, because the E2E suite
depends on it:

- `Application.recruiter` is `PROTECT`, so deleting demo users fails once
  applications exist. Each app registers a `register_resetter()` that clears its
  own rows first, highest order first.
- Seeders clear what the UI can change (`note_for_recruiters`, company profile
  fields, offices) and delete rows the E2E suite created (jobs not in the spec
  list), otherwise the second run sees the first run's data.
- `seed_demo` refuses to run with `DEBUG=False` unless `--force`: the accounts
  share a publicly known password.

## Talent Pool search, identity and CVs

`PhoneNumberField` without a default region can stringify a Vietnamese local
number as `+NoneNone`. The identity hash explicitly parses with region `VN`
before formatting E.164. A name-and-date hash needs both values; a candidate
without a date of birth correctly has an empty `name_dob_hash` even though one
Plan 11 example assertion said otherwise. Migration `0004_backfill_pool_search`
fills hashes and the `simple` search vector for candidates that predate the pool.

PDF and DOCX parsing sees embedded text only; scans need OCR in a later plan.
Legacy `.doc` files are accepted and stored when their OLE header is valid, but
the parser returns no suggested fields. CV parsing runs in Celery, so a worker
must be running (and restarted after registering a new task) to get suggestions.
Failed or unreadable parsing must never prevent saving the form manually.

Do not let pytest generate parametrized IDs from a DOCX byte string. ZIP entry
timestamps change across xdist workers, giving each worker a different test
collection and aborting the entire suite. Use stable explicit IDs. The demo
pool has 21 candidates total: 15 submitted and 6 pool-only. The earlier Plan 10
message-count example expected 31, while the current seeder generates 29; do
not add artificial chat messages just to match that stale example.
- `created_at` is `auto_now_add`, so back-dating demo rows needs a follow-up
  `.update()`.
- The seeder registry is a module-level global; a test that registers its own
  seeders must wrap them in `isolated_registry()` or it will run the real ones.

## Postgres specifics

`Extract(duration, "day")` does not work for a `DurationField`; the metrics in
`apps/jobs/queries.py` use `Extract(..., "epoch") / 86400`.
`CombinedExpression` is not exported from `django.db.models`.

## Settings guards

`SECRET_KEY` falls back to a development value, and `config/settings/base.py`
refuses to start with that value when `DEBUG` is off.

## mypy scope

`factory_boy`, `django-rules` and `django_filters` ship no usable stubs. Rather
than casting at every call site, `pyproject.toml` relaxes a few error codes for
test modules and `*.rules`/`*.filters` only. Application code stays strict.

## pytest

`django_assert_max_num_queries` counts everything inside the block — build the
API client before it, or `force_login`'s own queries land in the total (20
instead of 5).

Run the suite on the host with `uv run pytest`. Inside the container,
`docker compose exec api pytest` silently runs against **dev** settings: the
service sets `DJANGO_SETTINGS_MODULE=config.settings.dev`, and that environment
variable wins over `[tool.pytest.ini_options] DJANGO_SETTINGS_MODULE` in
`pyproject.toml`. Nothing in `config/settings/test.py` applies — Celery is not
eager, the cache is the real Redis, media is not a temp directory — and
`test_celery_app_is_configured` fails for no reason anyone can see. Pass
`--ds=config.settings.test` if the suite really has to run in the container.
