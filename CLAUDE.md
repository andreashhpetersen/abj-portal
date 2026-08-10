# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

The Python virtualenv is **not** in the repo. Activate it first or every Django
command fails:

```bash
source ~/.virtualenvs/abj-portal/bin/activate   # `workon abj-portal` in an interactive shell
```

Node is managed by nvm and the system Node is an unusable v12. Always:

```bash
export NVM_DIR="$HOME/.nvm" && . "$NVM_DIR/nvm.sh" && nvm use 24
```

`frontend/.nvmrc` pins 24, so a bare `nvm use` works inside `frontend/`.

## Commands

Backend (from `backend/`, venv active):

```bash
python manage.py runserver          # :8000
pytest                              # whole suite
pytest apps/accounts/tests/test_auth.py::test_me_requires_authentication   # single test
ruff check . && ruff format .
python manage.py makemigrations && python manage.py migrate
```

Frontend (from `frontend/`, Node 24 active):

```bash
npm run dev         # :5173, proxies /api → :8000
npm run typecheck   # tsc --noEmit
npm run build       # typecheck + production bundle into dist/
```

Both dev servers must run for the SPA to work.

## Architecture

**Settings are split three ways.** `config/settings/base.py` holds everything
shared and reads env vars through django-environ; `dev.py` and `prod.py`
override. `manage.py` defaults to `config.settings.dev`, while `wsgi.py`/`asgi.py`
default to `config.settings.prod`. `prod.py` deliberately has no defaults for
`DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, or `DATABASE_URL` — a missing value
must crash at boot rather than start an insecure server. When adding a setting,
put it in `base.py` unless it genuinely differs per environment.

**Auth is session-cookie based, not token based.** The SPA calls
`/api/auth/csrf/` to prime the `csrftoken` cookie, POSTs to `/api/auth/login/`
with an `X-CSRFToken` header, and thereafter the session cookie authenticates
everything. No JWT, nothing in localStorage. `src/api/client.ts` adds the CSRF
header automatically — route new API calls through it rather than calling
`fetch` directly.

**Same-origin in dev and prod.** Vite proxies `/api` to Django (see
`vite.config.ts`), so cookies behave in development exactly as in production
where both are served from one domain. CORS settings exist as a fallback for a
split-origin deployment; prefer keeping one origin.

**DRF is closed by default.** `DEFAULT_PERMISSION_CLASSES` is `IsAuthenticated`,
so a new endpoint is private unless it opts out. Two shared permissions live in
`apps/accounts/permissions.py`: `IsBusinessCommittee` (gates everything in
`shoprentals`) and `IsOwnerOrAdmin` (object-level, expects a `created_by` FK —
booking models should have one).

**The user model is custom and email-keyed.** `apps.accounts.User` drops
`username`; `USERNAME_FIELD` is `email`. `create_user(email=..., password=...)`,
never `username=`. Committee access is a Django group named by the
`ERHVERVSUDVALG_GROUP` constant — check `user.is_business_committee`, don't
query the group name inline.

**Not every user is a resident.** Employees and third-party managers get a
`User` with no `Resident` row, so never assume an address or resident number
exists — check `user.is_resident` (or the nullable `resident` key in the API).
Residency lives in `Resident`: `external_user_id` (the person's id in the
association's other database, which is the source of truth), `resident_number`
formatted `1-2345-6789-0` and regex-validated, plus the flat. The address is
structured, not free text: `Building` holds street and house number — the
association spans several blocks on more than one street — and `Resident` adds
floor and door. Use `resident.address` for display rather than reassembling it,
and `select_related("resident__building")` when listing users.

**Feature apps are pre-wired placeholders.** `apps/bookings` and
`apps/shoprentals` are in `INSTALLED_APPS` and mounted in `config/urls.py` with
empty `urlpatterns`. Their `models.py` docstrings record the domain rules from
the brief (private-booking 2-week horizon and admin toggle, recurrence,
attendance, application statuses) — read them before building either feature.

## Conventions

- Danish is the user-facing language: UI strings, API error messages, and admin
  `verbose_name`s are Danish. Code, comments, and identifiers are English,
  except domain terms with no clean translation (*erhvervsudvalg*,
  *beboerlokale*) which stay Danish everywhere.
- Timezone is `Europe/Copenhagen` with `USE_TZ=True`. Booking logic must be
  timezone-aware — use `django.utils.timezone`, never naive datetimes.
- Tests live in `apps/<app>/tests/` and use pytest-django (function-style with
  fixtures, not `TestCase` classes). Test names are full sentences describing
  the behaviour.
- Ruff enforces a 100-char line length; migrations are excluded.
