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

**Booking rules live on the model, not in views.** `Event.save()` calls
`full_clean()` deliberately: DRF's `ModelSerializer` does not, so without it the
API could store an overlapping booking that the admin would reject. Put new
booking invariants in `Event.clean()` and they hold everywhere — API, admin,
shell, import. The rules are: no overlap with a live event (cancelled ones free
the slot, and touching end-to-start is fine), public events need a title,
private ones must have none, and private bookings obey `BookingSettings`.

The private-booking policy is a **notice period, not a ceiling**: a resident
must book *at least* `private_booking_min_notice_days` ahead (14) and *at most*
`private_booking_max_horizon_days` (90), on a weekday listed in
`private_booking_weekdays`. The brief's phrase "booked 2 weeks in advance" was
originally implemented backwards as a ceiling — it is a floor. The window is
measured in whole calendar days via `timezone.localdate()`, not against a moving
timestamp, so a booking exactly 14 days out is valid at any hour. The weekday is
judged on the day the booking *starts*, so a Saturday party running to 02:00
counts as Saturday.

These rules apply **only when creating** (`self._state.adding`). Applying them
on every save would mean that closing private bookings left existing ones
impossible to cancel. Admins bypass all of them.

**Recurrence is materialised, not computed.** `EventSeries.create_occurrences()`
writes real `Event` rows, so the calendar stays a date-range query and a single
occurrence can be cancelled without special-casing the pattern. The arithmetic
in `occurrence_times()` runs on local wall-clock time and re-localises, so a
19:00 event stays at 19:00 across a DST change — don't "simplify" it to adding
`timedelta` to an aware datetime. Series expansion is capped at
`MAX_OCCURRENCES`.

**Cancellation is soft.** `Event.cancel(by=...)` sets `cancelled_at`/
`cancelled_by` and keeps the row; the slot frees up because
`_clashing_events()` ignores cancelled events. Filter on
`cancelled_at__isnull=True` when listing live bookings.

**One room is assumed.** `Event` has no room FK, and `_clashing_events()` is the
only place that assumption lives — adding a second bookable space means adding
the FK and including it in that filter.

**Serializers must translate model validation.** Because `Event.save()` calls
`full_clean()`, a rule violation reaching the database layer would surface as a
500. `EventSerializer.validate()` builds the instance the write would produce,
validates it, and re-raises Django's `ValidationError` as DRF's — that is what
turns "the room is already booked" into a 400 against the `start` field. Any new
booking serializer needs the same treatment; use `run_model_validation()`.

**List filters apply only to the list action.** `EventViewSet.get_queryset()`
returns early for detail routes. Filtering there once made cancelled bookings
404 on their own URL, so they could not be inspected, reinstated or deleted.

**The event list is unpaginated on purpose** — a month view bounded by
`?from=`/`?to=` must not have bookings silently truncated by a page size.

**`apps/shoprentals` is still a placeholder** in `INSTALLED_APPS` and
`config/urls.py` with empty `urlpatterns`; its `models.py` docstring records the
domain rules from the brief.

**The calendar UI is hand-built, no calendar library.** `src/lib/dates.ts` owns
every date decision: Monday-first weeks, and `dateKey()` composing
`YYYY-MM-DD` by hand rather than `toISOString().slice(0, 10)` — the latter is
UTC, so an evening booking in Copenhagen would land on the previous day. Use
these helpers instead of reaching for `Date` methods in components.

The calendar fetches the whole visible grid, padding days included, so a
booking on the 31st does not vanish when it falls in a neighbouring month's
row. Events are indexed by *every* day they touch (`daysCovered`), so a party
running past midnight appears on both days.

**The server decides permissions, the UI reflects them.** Each event carries
`can_cancel`, `is_attending` and `attendee_count`, so components render from
those rather than recomputing ownership rules client-side.

## Open decisions

Deliberately unresolved — don't quietly pick one while doing something else.

- **How residents get into the system.** A sync command against the
  association's other database, a one-off import, or manual admin entry. This
  decides whether `Resident.external_user_id` and `resident_number` should be
  read-only in the admin, whether a `synced_at` field is needed, and what
  happens when someone moves out. Until it is settled, residency is edited by
  hand in the admin and `external_user_id` is required.

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
