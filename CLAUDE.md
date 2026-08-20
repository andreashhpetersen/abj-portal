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

Deployment checks (from the repo root, Docker available):

```bash
docker build -f deploy/Dockerfile -t abj-portal .
./deploy/smoke.sh http://127.0.0.1:8011   # against a running container
```

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

**`apps/shoprentals` is organised around who owns which field.** That split is
the whole design, and breaking it is the way to do real damage here. The
applicant owns their answers, and the sync overwrites them on every run. The
committee owns status, rating, assignee, comments and the contract details, and
the sync must never write those — `Application.apply_form_data` is the only
writer of form-owned fields and touches nothing else. A sync that reset a rating
would be a sync nobody could safely run on a timer, so
`test_a_resync_does_not_touch_the_committees_own_work` is the test to keep
green. Rows that disappear from the sheet keep their applications, which by then
may carry months of notes.

**The shop-rental form is expected to change, so the schema does not mirror it.**
Every answer is stored verbatim and in order in `Application.answers`, and the UI
renders whatever arrives rather than naming questions. Only name, email and phone
are lifted into columns, by `HEADER_ALIASES` in `ingest.py` — *the* one place a
form change needs reflecting. A new question costs nothing; a reworded contact
question still stores its answer, leaves the column blank, and makes
`sync_applications` say so on stderr. Don't "tidy" this into typed columns per
question.

**Ingestion polls and reconciles; it does not replay.** Each
`manage.py sync_applications` run reads the whole sheet and upserts, so a failed
run, a mid-run deploy or a hand-edited cell all resolve on the next pass. That
self-healing is why polling was chosen over an Apps Script webhook, whose lost
deliveries look exactly like "nobody applied this week". Identity is a digest of
the submission's timestamp and email (`ingest.source_key`), because a responses
sheet has no id and a row number would re-point every key below it as soon as
someone sorted the sheet.

**`sheets.py` is the only code that touches Google**, and it imports the client
libraries inside the function so the rest of the portal runs, checks and tests
without them. Everything interesting lives in `ingest.py`, which takes a header
and rows from anywhere — that seam is what makes the ingestion testable without
credentials or a network.

**`Application.search_text` is a denormalised search column, not information.**
Django writes JSONField values with `ensure_ascii`, so a word like "Frisør"
reaches the column with its ø as a `\u`-escape, and an `icontains` against
`answers` silently matches nothing for exactly the words a Danish committee
searches for. `Application.save()` rebuilds it on every write so it cannot
drift — don't replace the filter with a JSON lookup.

**The five statuses are the committee's real workflow, not a tidy state machine.**
`Gemt til senere` exists because there is usually no vacant unit to offer a good
applicant at the moment they apply, and it is the state that makes the archive
worth keeping; `I gang` routinely lasts months, which is why `set_status()`
stamps `status_changed_at` — go through it rather than assigning `status`
directly. `rating` is nullable because unrated is not the same as one star.
Nothing is ever deleted: rubbish is `Afvist` so a reapplying applicant is
recognisable, which is also why the viewset has no create and no destroy.

**`ApplicationDetails` is filled in by hand, over weeks, and is never complete
early.** Every field is optional and the API reports `missing_fields` so the UI
can show a checklist. Its purpose is the document handed to the association's
lawyer to draft the lease — **that export is not built**, and the field list is a
starting point to be reconciled with what the lawyer actually asks for.

**Assignment is restricted to the erhvervsudvalg**, validated in
`ApplicationSerializer.validate_assignee_id` — note the name follows the
serializer field, not the model field, or DRF never calls it. The list of
assignable people comes from `User.objects.business_committee()`, so the rule for
who counts lives next to `is_business_committee` rather than being re-derived by
naming the group inline.

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

**Hosting is settled: UpCloud, Proton, Scaleway.** `INFRASTRUCTURE.md` records
the decision and, more usefully, what was rejected and why — the criterion is
European *ownership*, not merely European data residency, so DigitalOcean and
anything else under the US CLOUD Act is out. Don't reopen it casually; do read
it before adding a third-party service, because a US-hosted mailer or monitor
would quietly undo the whole point.

**Production runs one process for everything.** gunicorn serves the API and the
admin, and WhiteNoise serves the built SPA from the same process, so the browser
sees a single origin and the session and CSRF cookies behave exactly as they do
behind the Vite proxy. Caddy terminates TLS in front of it. `SERVE_SPA` gates
the SPA half — off in development, where Vite owns the frontend. See `README.md`
under *Deployment* for the shape and the pipeline.

**The SPA catch-all must keep excluding Django's own prefixes.** The last entry
in `config/urls.py` is `^(?!api/|admin/|static/).*$`. Without that lookahead a
mistyped API path answers `200` with HTML instead of `404` with JSON, and the
admin disappears behind the calendar. Simplifying the regex is the single
easiest way to break production silently.

**`index.html` is streamed, not rendered as a template, and never cached.** It
is Vite's output, so running it through the Django template engine would give
meaning to whatever brace sequences a future plugin emits. It is served
`no-cache` because it names the hashed asset bundles — a cached copy points at
files the next deployment has already replaced.

**`/api/health/` is exempt from `SECURE_SSL_REDIRECT`.** The container's own
`HEALTHCHECK` reaches gunicorn directly, so it arrives without
`X-Forwarded-Proto`; without `SECURE_REDIRECT_EXEMPT` it gets redirected to a
port nothing listens on and the container is declared unhealthy forever.

**Deployment assertions live in `deploy/smoke.sh`, not inline in a workflow.**
Three callers run it unchanged — CI against the freshly built image, the release
workflow against production, and a developer against a local container. Add new
expectations there so all three keep checking the same contract.

**The database is managed and deliberately single-node.** A second node buys
automatic failover, which a room-booking calendar does not need; what the portal
does need is retention, and a single node only keeps three days of
point-in-time recovery. `deploy/backup.sh` closes that gap with a nightly
`pg_dump`, encrypted to a public key so the server can write backups but not
read them back. The cheap plan is a considered trade, not an oversight —
`INFRASTRUCTURE.md` has the reasoning, including the restore test that has to
happen quarterly for any of it to mean anything.

## Open decisions

Deliberately unresolved — don't quietly pick one while doing something else.

- **How residents get into the system.** A sync command against the
  association's other database, a one-off import, or manual admin entry. This
  decides whether `Resident.external_user_id` and `resident_number` should be
  read-only in the admin, whether a `synced_at` field is needed, and what
  happens when someone moves out. Until it is settled, residency is edited by
  hand in the admin and `external_user_id` is required.

- **What the lawyer actually needs to draft a lease.** `ApplicationDetails` has a
  plausible field set and a `REQUIRED_FOR_CONTRACT` list, both guessed rather
  than asked for. Until someone checks with the association's lawyer, don't build
  the export on top of them — a document generator pinned to the wrong fields is
  harder to correct than the fields themselves. Adding or removing one is a
  migration and a line in `REQUIRED_FOR_CONTRACT`, nothing more.

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
