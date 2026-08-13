# Beboerportal

Web app for an *andelsboligforening* (Danish housing cooperative). Two planned
features: booking of the community room, and handling of shop-rental
applications for the business committee.

Django REST API + React SPA. Both currently ship a working login and empty
feature sections.

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
python manage.py seed_demo            # optional: demo residents and bookings
python manage.py runserver            # http://127.0.0.1:8000
```

`seed_demo` fills the calendar with plausible Danish data — residents across
three buildings, private and public bookings, a recurring café, one cancelled
booking, and an employee account with no residency. Every account uses the
password `beboer1234`, so the command refuses to run unless `DEBUG=True`. Rebuild
it any time with `python manage.py seed_demo --reset`.

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
| Type-check               | `frontend/` | `npm run typecheck`                         |
| Production build         | `frontend/` | `npm run build` → `frontend/dist/`          |

## Layout

```
backend/
  config/            Django project: settings/{base,dev,prod}.py, urls.py, wsgi.py
  apps/accounts/     Custom email-based User, residency, auth endpoints, permissions
  apps/bookings/     Feature 1 — community room. Models + admin, no API yet.
  apps/shoprentals/  Feature 2 — shop rentals. Placeholder.
frontend/
  src/api/           fetch wrapper (cookies + CSRF)
  src/auth/          AuthContext — holds the logged-in member
  src/pages/         One component per route
```

## Users and residents

A login and a residency are separate things. `User` is anyone who can sign in;
`Resident` records that the person lives here — their id in the association's
other database, their resident number (`1-2345-6789-0`), and their flat.
Employees and third-party managers get a `User` with no `Resident`, so any code
touching an address must handle its absence.

Addresses are structured: `Building` holds a street and house number (the
association covers several blocks across more than one street) and `Resident`
adds floor and door. Both are managed in the admin — create the buildings once,
then residency is edited inline on each user.

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

## Deployment

Not set up yet. The intended target is DigitalOcean App Platform with managed
Postgres:

* Routing: `/api`, `/admin` and `/static` must reach Django; everything else
  falls through to the SPA's `index.html`, since the frontend owns its own
  routes
* API: `gunicorn config.wsgi` with `DJANGO_SETTINGS_MODULE=config.settings.prod`
* Static files: collected by WhiteNoise (`python manage.py collectstatic`)
* SPA: `npm run build`, served as a static site on the same domain so no CORS
  exemption is needed
* Release phase: `python manage.py migrate`
