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
python manage.py import_residents data/register/users-YYYY-MM-DD.csv --dry-run
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
The address is structured, not free text: `Building` holds street and house
number — the association spans several blocks on more than one street — and
`Resident` adds floor and door. Use `resident.address` for display rather than
reassembling it, and `select_related("resident__building")` when listing users.

**`Resident` carries two numbers and neither is unique.** `unit_number`
(`Bolignr.`) is the flat; `resident_number` (`Beboernr.`) is the tenancy, which
everyone living in the flat shares — so a couple is two logins against one
number, and a unique constraint on either would lock the second person out.
`unit_number` is the link back to the register, because the unit outlives the
tenancy. Both are `blank=True`: a household member has no tenancy number at all.
The format validators are deliberately loose (`\d-\d{3,5}-\d{3,5}`), since the
real register contains `1-1121-409-2` and `1-1121-5007-10` beside the usual
shape. There is no id-of-this-person-elsewhere column, because the register has
none to give.

**The resident register is imported, not synced.** An export from INNA (the
administration company, Cobblestone under its old name — hence `Role hos CS`) is
reconciled into `RegisterEntry`. INNA has no API, so somebody fetches the file by
hand and there is no timer.

**The board uploads it in the admin; the management command is the same import
for anyone with a shell.** The people who can get the export out of INNA are not
the people with SSH, so *Beboerregister → Importér beboerregister* is the real
route and `manage.py import_residents <file.csv>` is the fallback. Both go
through `register.run_import`, and both render `register.*_notes`, so they say
the same thing about the same file — put new diagnostics there, not in either
caller. The upload is gated on its own `import_register` permission rather than
`change_registerentry`: every column here is read-only, and what it grants is
replacing the register wholesale, and with it who the portal admits.

**The export is never stored.** An upload is read out of the request and dropped;
`csvsource.MAX_BYTES` keeps it under `FILE_UPLOAD_MAX_MEMORY_SIZE` so it does not
even spill to a temporary file. That is why a dry run cannot be confirmed with a
second click — there would be nothing left to confirm against — and the cost, one
extra file-picker, is the right trade for not having six hundred residents'
details sitting on a disk. `backend/data/register/` is for local development
only, gitignored *and* dockerignored; the only register data in the repo is the
invented `apps/accounts/tests/data/register_sample.csv`.

**Three modules, one seam each.** `csvsource.py` is the only code that knows the
register arrives as a CSV — the counterpart of `shoprentals/sheets.py`, and what
changes the day INNA offers an API. It detects the encoding and separator rather
than asking, by trying combinations until the required columns appear, because a
file re-saved in Excel on a Danish machine is cp1252 with semicolons and the
person uploading should not have to know that. `register.py` takes a header and
rows from anywhere, which is what makes all of it testable without a real dump.
`forms.py` turns a bad file into a field error next to the input.

**A `RegisterEntry` is not an account, and the import creates none.** Most of
the register has never opened the portal and thirty flats have no email address
in it at all. What the import produces is the list a signup is checked against.
Identity is `(unit_number, name_key)` — the export has no id for a person, and
a line number would re-point every row below it the moment somebody sorted the
file. Nothing is deleted: a row that stops appearing goes `is_current=False`,
because the account may own bookings by then and a disappearance is a question
rather than an answer.

**Eligibility is stored, not recomputed, and it needs the whole export.** The
rule is "an andelsbolig and a `Beboernr.`", which covers andelshavere,
subletters and board members. Household members are the exception that stops it
being a one-liner: the export gives them neither column, so they qualify on
whether their `Bolignr.` is a unit some *other* row shows to be residential —
which is why `mark_eligibility` runs over all the rows at once, and why the
shopkeeper's partner at `Jægersborggade 57, kld. th.` correctly stays out. An
address that cannot be parsed is never eligible, whatever else the row says.
`Enhedstype` `Bolig` counts as `Andelsbolig` (three flats are typed that way),
and `Indflytningsdato` deliberately decides nothing — the export contains
move-ins months ahead, and somebody taking over a flat in December has every
reason to book the beboerlokale in November.

**Signup is a claim; the register clears it when it can, otherwise a human
does.** `/api/auth/signup/` is the only endpoint an anonymous visitor can write
through. `register.auto_approve` resolves the claimed number — against both
`Beboernr.` and `Bolignr.`, since a household member only has the latter — and
if it lands on exactly one eligible unit the account is activated with a
`Resident` row attached, on the spot. Anything else creates the `User` with
`is_active=False` plus a pending `SignupRequest` for a board member to approve
in the admin, which is what let the portal offer signup before the register
existed at all.

What that trades away is worth stating plainly: the claimed email is **not**
part of the test — it cannot be, since thirty eligible flats have no email in
the register — so the proof is only that the claimant has seen a rent statement
for a flat here. The per-IP `signup` throttle is therefore load-bearing in a way
it was not before: it is what stops the form being used to enumerate which
numbers are real. Ambiguity is always a refusal, never a guess.

**Signup answers the same 202 however it went**, including when it activated the
account. A `201` on a match and a `202` otherwise would turn the form into an
oracle for testing resident numbers, which is exactly what the uniform answer
exists to prevent — so the receipt sends everyone to the login page instead, and
`LoginView._rejected` names the real reason only to somebody who already has the
password.

The claim is **free text on `SignupRequest`, never written to a `Resident` row**:
what the applicant typed is the thing being checked, and storing it as residency
would destroy the only record of it. A residency comes from the register or from
a board member, never from the form. The claimed number is deliberately not
format-validated either — the register either recognises it or a board member
reads it, and rejecting a mistyped digit teaches the applicant nothing.

**A claim the register refuses can still be approved against a row chosen by
hand.** *Brugeranmodninger → Godkend, og knyt til en række i beboerregistret*
opens one request on a page that searches the eligible entries and links the
account to the one a board member picks: `register.link_and_approve`, the
counterpart of `auto_approve` for every case it refuses — a mistyped number, a
number reaching two units, a flat the export has not caught up with. It exists
because `SignupRequest.approve()` on its own only activates the account, and
since nothing in the portal gates on residency, an approval with no address is
invisible until months later, when a booking cannot be placed in a flat.

Three things about it are load-bearing. The claimed number is never corrected
in place — it is the thing that was checked — so what the account was actually
linked to goes into `review_note` instead, which is the only record that
anything was corrected at all. Only *eligible* entries can be chosen, because a
shop or somebody who has moved out is not a typo; a residency that genuinely
has to go outside the rule is typed on the user, where it is visibly a person's
decision rather than a match. And it is one request at a time: there is no bulk
version of recognising somebody, so a selection of five is refused rather than
looped over.

**The signup page is for residents, so the resident number is required**
(`phone` is the only optional field). Someone with no number to give is an
employee or a third-party manager, and the board creates those accounts in the
admin — the only place that can grant more than a login anyway. Required but
unvalidated is the deliberate combination: without a number the board has only
an address, which can match a flat with two names on the door. The column stays
`blank=True` so a request the board enters by hand is still valid.

A field error is the one answer signup gives other than the uniform 202, and it
is safe to: it describes what the submitter typed, not who already has an
account here. The frontend states the password rules up front rather than
letting `AUTH_PASSWORD_VALIDATORS` reject four times in a row — `SignupPage`
restates that setting and has to be kept in step with it.

`reject()` deletes the provisional account and keeps the request as a record,
because `User.email` is unique: a pending request otherwise holds an address
hostage, and someone signing up as a resident who has not got around to it yet
would lock out the real one. Withdrawing an *approved* account is
`is_active = False`, not `reject()` — by then it may own bookings a cascade would
take with it.

The `website` honeypot and the per-IP `signup` throttle scope are the whole bot
defence. That used to be enough because an unapproved account could do nothing;
now that a matched claim activates itself, the throttle is also what keeps the
form from being used to enumerate resident numbers, so treat loosening it as a
security change. Note its effective limit is the configured rate times the
gunicorn worker count, since there is no shared `CACHES`. A CAPTCHA is not an
easy option here —
reCAPTCHA is Google's and Turnstile is Cloudflare's, both against the ownership
criterion in `INFRASTRUCTURE.md`; Friendly Captcha is the compliant escalation if
one is ever needed.

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

These rules apply when the booking is **placed** — created, moved to another
day, or turned from public into private. `Event._is_being_placed` is where that
is decided, and `Event.from_db` is what remembers the before-state it compares
against. They deliberately do *not* apply to the rest of an edit: retiming
within the booked day, correcting the finishing time, or cancelling. Both
halves of that are load-bearing. Testing them on every save would leave an
existing booking uncancellable once private bookings closed, and uneditable the
moment it came within its own notice period. Testing them only on create would
make the edit route a way round the notice period — book fourteen days out, then
move it to tomorrow. Admins bypass all of them.

**Editing belongs to whoever booked**, and a cancelled booking cannot be edited
at all: `_clashing_events()` ignores cancelled events, so an edit would not
reclaim the slot and must not look as though it had. `EventSerializer` refuses
it, and `can_edit` tells the UI the same thing.

**An occurrence of a series edits as an ordinary booking.** One week the
strikkecafé moves or is cancelled and the rest of the term is unaffected, which
is why occurrences are materialised in the first place. `EventEditForm` offers
the series-wide alternative alongside it and defaults to this one, because it is
the choice that cannot surprise anybody. A change of *date* is always
single-occurrence: moving the whole series to another weekday is a change of
rhythm, which is the thing series editing does not do.

**Recurrence is materialised, not computed.** `EventSeries.create_occurrences()`
writes real `Event` rows, so the calendar stays a date-range query and a single
occurrence can be cancelled or moved without special-casing the pattern. The
arithmetic in `occurrence_times()` runs on local wall-clock time and
re-localises, so a 19:00 event stays at 19:00 across a DST change — don't
"simplify" it to adding `timedelta` to an aware datetime, and use
`shift_wall_clock()` for the same reason when moving one. Series expansion is
capped at `MAX_OCCURRENCES`.

**`seed_start`/`seed_end` are the pattern's origin, not a copy of the first
occurrence.** Occurrences can be moved one at a time, so they cannot be trusted
to say where the pattern began — without the columns, extending a series would
re-materialise it from wherever somebody happened to drag the first evening.
They are nullable only because series created before them exist; `pattern_seed()`
falls back to the earliest occurrence for those.

**A series can be renamed, retimed and made to run longer or stop sooner — but
never re-rhythmed.** `EventSeriesUpdateSerializer` leaves `frequency` and
`interval` read-only, because changing them means deleting future occurrences
and recreating them, and `EventAttendance.event` is `CASCADE` — everyone who had
signed up would go with them. That is a decision for the board, not a default,
so delete-and-recreate is still the way. What the four operations do:

* `retitle_occurrences()` pushes the series' words onto its upcoming
  occurrences, skipping any that no longer match the series' *previous* words —
  those have been retitled by hand and say something the series does not.
* `shift_occurrences()` moves them by a **delta**, not to an absolute hour, so
  an evening somebody moved to another day stays moved. The seed moves too, or
  a later extension would put the new evenings back at the old hour.
* `extend()` only ever appends. The gap left by a cancelled or moved occurrence
  is a decision somebody made, and refilling it would quietly undo them.
* `cancel_beyond()` soft-cancels what a shorter `until` no longer covers, for
  the same attendance reason.

All four skip past and cancelled occurrences: an evening that has happened is a
record of what happened. The update is atomic — a clash halfway through would
leave the series meeting at two different times. Note that only a *retime* can
fail that way; a rename cannot create a clash, since `_clashing_events()`
compares times and excludes the row itself.

**`EventSeriesAdmin.save_model` carries a series edit through to the calendar.**
Occurrences are real rows, so editing the series there used to change nothing a
resident could see — the admin reported success and the calendar kept the old
title.

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
UTC, so an evening booking in Copenhagen would land on the previous day.
`timeKey()` is the same idea for the `HH:mm` the edit form fills its pickers
from. Use these helpers instead of reaching for `Date` methods in components.

The calendar fetches the whole visible grid, padding days included, so a
booking on the 31st does not vanish when it falls in a neighbouring month's
row. Events are indexed by *every* day they touch (`daysCovered`), so a party
running past midnight appears on both days.

**The grid and the event list are two readings of one fetch.** `CalendarPage`
holds the month, the events and the view; `MonthGrid` draws when the room is
taken and `PublicEventList` the shared arrangements worth turning up to, and
switching between them rearranges events already in hand rather than asking the
API again — which is why the list narrows the grid's range to the month itself
instead of taking `?category=public`. A row is filed under the day it *starts*,
the same way the booking rules judge a weekday. `MonthNav` is shared so the two
views step through the month with one control, and `EventDetails` is shared so
attending, editing and cancelling exist once — including `EventEditForm`, which
is why a booking can be corrected from the list as readily as from the day
panel. What each of those may do is decided by the server and read off the
event, so a second copy is a second chance to get that wrong.

**The server decides permissions, the UI reflects them.** Each event carries
`can_cancel`, `can_edit`, `is_attending` and `attendee_count`, so components
render from those rather than recomputing ownership rules client-side. The one
rule the client does restate is the private-booking policy, in
`src/lib/policy.ts` — a form that says up front why a day is closed beats one
that waits for a rejected submit — and both the booking form and the edit form
read it from there rather than each keeping a copy.

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
the SPA half — off in development, where Vite owns the frontend. See `OPERATIONS.md`
for the shape and the release pipeline.

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

- **What a move-out should do.** Settled: `import_residents` flags the register
  row `is_current=False` and stops it admitting anyone, and leaves the `User`
  and `Resident` alone — a cascade would take the person's bookings with it.
  What is *not* settled is who deactivates the account and when. Today nobody
  does, so a resident who has moved keeps a working login until a board member
  notices the warning in the import's output. Deciding it means deciding whether
  a departed resident should lose access at all (they may still owe a cleaning
  fee on a booking) and after how long.

- **Whether erhverv tenants should get in.** The shops are in the register with
  `Enhedstype` `Erhverv` and are deliberately ineligible; the board's rule is
  that living here is what entitles someone to the beboerlokale. Ask before
  widening it — `RESIDENTIAL_UNIT_TYPES` is a one-line change and the
  consequences are not.

- **Whether a series should be able to change its rhythm.** Renaming, retiming
  and extending one all work; changing `frequency` or `interval` does not,
  because re-materialising the occurrences would take the attendance rows of
  everyone who had signed up for the evenings that disappear. Deciding it means
  deciding what those sign-ups are worth — carry them to the nearest new
  occurrence, drop them silently, or mail the people concerned. Until then,
  delete and recreate, which at least makes the loss visible.

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
