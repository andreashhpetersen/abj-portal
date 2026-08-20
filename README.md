# Beboerportal

Web app for **AB Jæger**, a Danish *andelsboligforening* (housing cooperative)
whose flats span several blocks across more than one street. Two features:
booking of the community room, and handling of shop-rental applications for the
business committee.

Django REST API + React SPA, served as a single origin in production.

| Document              | What it covers                                          |
| --------------------- | ------------------------------------------------------- |
| This file             | What the portal does, and how to develop it              |
| `OPERATIONS.md`       | Running it on the server: releases, setup, the sync      |
| `INFRASTRUCTURE.md`   | Why the hosting is what it is, and what is outstanding   |
| `CLAUDE.md`           | Design decisions worth not undoing                       |

## Requirements

| Tool   | Version | Notes                                                        |
| ------ | ------- | ------------------------------------------------------------ |
| Python | 3.10+   | Virtualenv `abj-portal` (`workon abj-portal`)                 |
| Node   | 24 LTS  | `nvm use` in `frontend/` picks it up from `.nvmrc`            |

## Getting started

Two terminals — the API and the SPA run as separate dev servers.

**Backend**

```bash
workon abj-portal                     # or: source ~/.virtualenvs/abj-portal/bin/activate
cd backend
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py createsuperuser      # asks for email, not username
python manage.py seed_demo            # optional: demo residents, bookings, applications
python manage.py runserver            # http://127.0.0.1:8000
```

`seed_demo` fills the database with plausible Danish data — residents across
three buildings, private and public bookings, a recurring café, one cancelled
booking, an employee account with no residency, and six shop-rental applications
spread across the workflow. Every account uses the password `beboer1234`, so the
command refuses to run unless `DEBUG=True`. Rebuild it any time with
`python manage.py seed_demo --reset`, which removes only what it created.

**Frontend**

```bash
cd frontend
nvm use                               # reads .nvmrc → Node 24
npm install
npm run dev                           # http://127.0.0.1:5173
```

Open http://127.0.0.1:5173 and log in with the superuser you just created.
Vite proxies `/api`, `/admin` and `/static` to Django, so the browser sees one
origin and the session cookie works exactly as it will in production.

The Django admin — where members, committee membership and the booking policy
are managed — is at http://127.0.0.1:5173/admin/ (or straight at
http://127.0.0.1:8000/admin/). Staff accounts get an *Administration* link in
the app's header, and the session is shared: logging into the portal logs you
into the admin.

## Commands

| What                     | Where       | Command                                     |
| ------------------------ | ----------- | ------------------------------------------- |
| Run tests                | `backend/`  | `pytest`                                    |
| Run one test             | `backend/`  | `pytest apps/accounts/tests/test_auth.py::test_login_returns_the_member_and_starts_a_session` |
| Lint / format            | `backend/`  | `ruff check .` · `ruff format .`            |
| Make migrations          | `backend/`  | `python manage.py makemigrations`           |
| Sync shop-rental form    | `backend/`  | `python manage.py sync_applications`        |
| Type-check               | `frontend/` | `npm run typecheck`                         |
| Production build         | `frontend/` | `npm run build` → `frontend/dist/`          |

## Layout

```
backend/
  config/            Django project: settings/{base,dev,prod}.py, urls.py, wsgi.py
  apps/accounts/     Custom email-based User, residency, auth endpoints, permissions
  apps/bookings/     Feature 1 — community room: models, rules, API
  apps/shoprentals/  Feature 2 — shop rentals: models, Google Forms sync, API
frontend/
  src/api/           fetch wrapper (cookies + CSRF), one module per feature
  src/auth/          AuthContext — holds the logged-in member
  src/components/    Calendar grid, booking form, application list and detail
  src/lib/dates.ts   Every date decision the calendar makes
  src/pages/         One component per route
```

## Users and residents

A login and a residency are separate things. `User` is anyone who can sign in;
`Resident` records that the person lives here — their id in the association's
other database, their resident number (`1-2345-6789-0`), and their flat.
Employees and third-party managers get a `User` with no `Resident`, so any code
touching an address must handle its absence.

Addresses are structured: `Building` holds a street and house number (AB Jæger
spans several blocks, so a street name alone does not identify a flat) and
`Resident` adds floor and door. Both are managed in the admin — create the
buildings once, then residency is edited inline on each user.

## Booking the community room

There is one room, and two live bookings may never overlap. Bookings are either
**private** (no title — the calendar shows who booked it and how to reach them)
or **public** (title, description, and residents can sign up to attend).

Private booking is governed by a policy row edited in the admin under *Booking
settings*:

* whether private booking is open at all,
* a **notice period** — a resident must book at least this far ahead, 14 days
  by default, so neighbours know the room is spoken for,
* a **horizon** — and no further ahead than this, 90 days by default, so nobody
  blocks the calendar a year out,
* which **weekdays** the room may be booked privately.

Admins are bound by none of them and can book on a resident's behalf. The
window counts whole calendar days, so a booking exactly 14 days out is fine
whatever the time of day. Cancelling is soft — the booking is kept and marked
cancelled, and its slot becomes free again.

A public event can repeat daily, weekly or monthly with an interval, up to an
end date. Occurrences are created as ordinary bookings, so any single one can be
moved or cancelled on its own.

### API

All of it requires a logged-in user.

| Method            | Path                              | Who                |
| ----------------- | --------------------------------- | ------------------ |
| `GET`             | `/api/bookings/events/`           | anyone             |
| `POST`            | `/api/bookings/events/`           | anyone             |
| `GET`             | `/api/bookings/events/{id}/`      | anyone             |
| `PATCH`/`PUT`     | `/api/bookings/events/{id}/`      | owner or admin     |
| `DELETE`          | `/api/bookings/events/{id}/`      | admin              |
| `POST`            | `/api/bookings/events/{id}/cancel/` | owner or admin   |
| `POST`/`DELETE`   | `/api/bookings/events/{id}/attendance/` | anyone       |
| `GET`/`POST`      | `/api/bookings/series/`           | anyone (delete: admin) |
| `GET`             | `/api/bookings/settings/`         | anyone             |
| `PATCH`           | `/api/bookings/settings/`         | admin              |

The calendar takes `?from=YYYY-MM-DD&to=YYYY-MM-DD` (inclusive local dates,
matching anything that overlaps the window), plus `?category=` and
`?include_cancelled=true`. It is not paginated — the date window bounds it.

Deleting is admin-only by design: everyone else cancels, which keeps the record
of who had booked and why the slot came free.

## Shop-rental applications (erhvervslejemål)

Candidates apply through a public **Google Form**. The portal reads the form's
responses sheet on a timer and gives the erhvervsudvalg somewhere to work on
what arrives: a status, a 1–5 rating, comments, an assignee, and a place to
collect the concrete facts a lease needs.

Nobody outside the erhvervsudvalg can see any of it.

Setting up the Google service account, the credentials and the timer is in
`OPERATIONS.md`.

### The workflow

Five statuses, and the reason each exists:

| Status               | Means                                                  |
| -------------------- | ------------------------------------------------------ |
| **Ny**               | Arrived, nobody has looked at it. The default.          |
| **I gang**           | Someone is in contact. Routinely lasts months.         |
| **Gemt til senere**  | Good applicant, no vacant unit to offer. Come back to it. |
| **Afvist**           | Not interesting. Filed, not deleted.                   |
| **Afsluttet**        | Done with — contract signed or otherwise concluded.     |

*Gemt til senere* is the one that makes the list worth keeping: there is usually
nothing free to offer a good applicant at the moment they apply. Nothing is ever
deleted, so an applicant reappearing next year is recognisable.

An unrated application is not the same as a one-star one, so the rating is empty
until someone sets it, and clicking the current rating clears it again. Status
changes are timestamped, so the UI can say *gemt til senere i 47 dage* — the
committee's own complaint is that this stage drags.

### Ingestion

`apps/shoprentals/ingest.py` maps a sheet into applications; `sheets.py` is the
only code that talks to Google.

```bash
python manage.py sync_applications
python manage.py sync_applications --dry-run   # parse and report, write nothing
```

**Polling, not a webhook, and reconciling rather than replaying.** Every run
reads the whole sheet and upserts. A failed run, a deploy mid-run, a fortnight
of downtime or a cell corrected by hand in the sheet all sort themselves out on
the next pass — worth much more here than seconds-fresh delivery, given that
these applications take weeks to become contracts.

**The form is expected to change, so the schema does not mirror it.** Every
answer is stored verbatim and in order, and the UI renders whatever arrives.
Only the three fields the committee filters and searches on — name, email,
phone — are lifted into columns, by the alias table `HEADER_ALIASES` in
`ingest.py`. That table is the *only* place a form change needs reflecting: a
brand-new question costs nothing, and a reworded contact question still stores
its answer, leaves the column blank, and makes the sync say so on stderr.
Questions match on their **first line**, because a Google Form question carries
its description below the title and the sheet flattens both into one heading.

`IGNORED_HEADERS`, in the same file, drops questions that are plumbing rather
than application data — the form's arithmetic anti-spam question. Removing an
entry brings the column back on the next sync; nothing has to be re-imported.

**A sync never touches the committee's work.** It writes only the applicant's
own fields. Status, rating, assignee, comments and contract details are not ours
to move, and a sync that reset a rating would be one nobody could safely run.
Rows that vanish from the sheet keep their applications, which by then may carry
months of notes.

Identity comes from a digest of the submission's timestamp and email, because a
responses sheet has no id of its own and a row number would re-point every key
below it the moment someone sorted the sheet. The consequence: correct an
applicant's email *in the portal*, not in the sheet — changing it upstream files
the next sync as a new application.

### Preparing the contract

Each application has a *Kontraktoplysninger* record: CVR, legal form, contact
person, which unit, what it may be used for, area, rent, deposit, wanted
handover, and free-text notes. None of it comes from the form — the form asks
only enough to judge whether someone is worth talking to, and the rest arrives
over weeks of correspondence. So every field is optional and a half-filled
record is the normal state, saved a field at a time.

The API reports `missing_fields`, the labels of what is still outstanding, which
the UI shows as a checklist. **Generating the document for the lawyer is not
built** — the record is shaped so that it is a formatting job, but there is no
export yet, and the field list is a starting point that should be reconciled
with what the lawyer actually asks for.

### API

Every route requires membership of the erhvervsudvalg.

| Method          | Path                                              |
| --------------- | ------------------------------------------------- |
| `GET`           | `/api/shop-rentals/applications/`                 |
| `GET`           | `/api/shop-rentals/applications/summary/`         |
| `GET`           | `/api/shop-rentals/applications/{id}/`            |
| `PATCH`         | `/api/shop-rentals/applications/{id}/`            |
| `GET`/`POST`    | `/api/shop-rentals/applications/{id}/comments/`   |
| `DELETE`        | `/api/shop-rentals/comments/{id}/`                |
| `GET`/`PATCH`   | `/api/shop-rentals/applications/{id}/details/`    |
| `GET`           | `/api/shop-rentals/members/`                      |

`PATCH` on an application accepts `status`, `rating` and `assignee_id` and
nothing else: the applicant's answers are a record of what was submitted, and
the sync would overwrite an edit anyway. There is no `POST` and no `DELETE` —
applications exist because someone filled in the form.

The list takes repeatable `?status=`, plus `?assignee=` (a member id or
`unassigned`), `?min_rating=`, `?q=` and `?ordering=`. `?q=` searches the mapped
fields *and* every answer, so a word from any question finds its application.
Sorting by rating keeps unrated ones last in both directions. Comments may be
deleted by their author or an admin, and cannot be edited — a months-long thread
that can be rewritten afterwards is not a record.

## Access control

* **Members** — anyone with an account. See the calendar.
* **Erhvervsudvalg** — members of the Django group `erhvervsudvalg`. Only they
  see shop-rental applications. Add members via the admin under Groups.
* **Admins** — `is_staff`. Can edit and delete everything.

Enforced server-side by `apps/accounts/permissions.py`. The frontend hides what
you cannot use, but the API is the actual boundary.

## Configuration

Settings are split by environment. `manage.py` defaults to
`config.settings.dev`; `wsgi.py` defaults to `config.settings.prod`. Values come
from environment variables (see `.env.example`) — production fails to boot if
`DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, or `DATABASE_URL` is missing, which
is intentional.

The three `SHOPRENTALS_*` variables are the exception to that rule: they default
to empty on purpose, so the portal runs perfectly well with no Google setup and
only `sync_applications` complains.

Note that `.env.example` is a committed template and is **never read**. The file
Django loads is `.env` at the repository root, which is gitignored.

## Deployment

One image holds both halves of the app: gunicorn serves the API and the admin,
WhiteNoise serves the built SPA from the same process, and Caddy terminates TLS
in front of it. A push to `main` runs the checks, builds the image, and releases
it to the server.

`OPERATIONS.md` has the detail — the release pipeline, rolling back, server
setup, the shop-rental sync, and the interim arrangements still in force until
DNS is ready. `INFRASTRUCTURE.md` has the reasoning behind the hosting choices.
