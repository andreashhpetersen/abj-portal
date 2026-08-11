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
python manage.py runserver            # http://127.0.0.1:8000
```

**Frontend**

```bash
cd frontend
nvm use                               # reads .nvmrc → Node 24
npm install
npm run dev                           # http://127.0.0.1:5173
```

Open http://127.0.0.1:5173 and log in with the superuser you just created.
Vite proxies `/api` to Django, so the browser sees one origin and the session
cookie works exactly as it will in production.

The Django admin at http://127.0.0.1:8000/admin/ is where members and committee
membership are managed.

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
settings*: whether private booking is open at all, and how far ahead residents
may book (14 days by default). Admins are not bound by either, so they can book
on someone's behalf. Cancelling is soft — the booking is kept and marked
cancelled, and its slot becomes free again.

A public event can repeat daily, weekly or monthly with an interval, up to an
end date. Occurrences are created as ordinary bookings, so any single one can be
moved or cancelled on its own.

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

* API: `gunicorn config.wsgi` with `DJANGO_SETTINGS_MODULE=config.settings.prod`
* Static files: collected by WhiteNoise (`python manage.py collectstatic`)
* SPA: `npm run build`, served as a static site on the same domain so no CORS
  exemption is needed
* Release phase: `python manage.py migrate`
