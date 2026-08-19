# Beboerportal

Web app for **AB Jæger**, a Danish *andelsboligforening* (housing cooperative)
whose flats span several blocks across more than one street. Two planned
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

Addresses are structured: `Building` holds a street and house number (AB Jæger
spans several blocks, so a street name alone does not identify a flat) and
`Resident` adds floor and door. Both are managed in the admin — create the buildings once,
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

The target is **UpCloud** — a single cloud server plus Managed PostgreSQL —
with Proton for AB Jæger's own mailboxes and Scaleway Transactional Email
for mail the portal sends itself. `INFRASTRUCTURE.md` records why, including
what was rejected and what still needs verifying.

Nothing is provisioned yet; the pipeline below is written and unexercised.

### Shape

One image holds both halves of the app. Gunicorn serves the API and the
admin, and WhiteNoise serves the SPA bundle from the same process, so the
browser sees a single origin and the session and CSRF cookies behave exactly
as they do behind the Vite proxy in development. Caddy terminates TLS in
front of it and renews certificates by itself, which is why no managed load
balancer is needed at this size. The database is UpCloud's, reached through
`DATABASE_URL`.

```
Caddy (TLS, :443) ──> gunicorn ──> UpCloud Managed PostgreSQL
                        │
                        ├─ /api/, /admin/  Django
                        ├─ /static/        WhiteNoise (admin + DRF assets)
                        └─ everything else SPA bundle, index.html fallback
```

The catch-all lives in `config/urls.py` behind the `SERVE_SPA` setting, and
excludes `api/`, `admin/` and `static/` — without that exclusion a missing
API route would answer `200` with HTML instead of `404` with JSON.

| File                         | Purpose                                  |
| ---------------------------- | ---------------------------------------- |
| `deploy/Dockerfile`          | Builds the SPA, then the app image       |
| `deploy/docker-compose.yml`  | The stack as it runs on the server       |
| `deploy/Caddyfile`           | TLS and reverse proxy                    |
| `deploy/backup.sh`           | Nightly encrypted `pg_dump` off-server   |
| `deploy/smoke.sh`            | Asserts a running portal serves properly |
| `.github/workflows/ci.yml`   | Checks on pull requests                  |
| `.github/workflows/deploy.yml`| Test, build, release on push to `main`   |

### Pipeline

`deploy.yml` runs the same lint, test and typecheck jobs CI runs, and only
then builds. It pushes the image to ghcr.io tagged with the commit SHA, pins
that tag in the server's `.env`, runs `migrate` against the new image as a
release phase — a failure there aborts with the old container still serving —
brings the stack up, and polls `/api/health/` until it answers. Releases are
serialised and never cancelled mid-flight, since a half-finished deploy can
leave migrations applied against the previous image.

Expect a few seconds of downtime while the container is replaced. Rolling
that to zero needs a second app node and a load balancer, which the launch
scope does not justify.

### First-time server setup

1. Create the cloud server and the Managed PostgreSQL instance, and point the
   portal's DNS record at the server.
2. Install Docker, plus `age` and `rclone` for backups.
3. `docker login ghcr.io` with a token carrying `read:packages`, so the
   server can pull the image.
4. Create `/opt/abj-portal/` holding `docker-compose.yml`, `Caddyfile`,
   `backup.sh` and a `.env` built from `.env.example`.
5. Schedule the backup: `17 3 * * *  /opt/abj-portal/backup.sh`, with cron
   mail going somewhere a person reads.
6. Add the repository secrets and variable listed at the top of
   `.github/workflows/deploy.yml`, then push to `main`.

**Restore-test the backups quarterly.** The database runs on a single node
with three days of point-in-time recovery, and the nightly dump is what
covers anything older. An untested dump is not a backup — `deploy/backup.sh`
documents the restore.

### Smoke-testing the image locally

Worth doing before the first real deploy, since this exercises the single-origin
arrangement and the SPA fallback that `runserver` never sees. Port 8011 rather
than 8000, so it does not collide with a dev server you have running:

```bash
docker build -f deploy/Dockerfile -t abj-portal .
docker run -d --name portal-smoke -p 8011:8000 \
  -e DJANGO_SECRET_KEY=local-smoke-test \
  -e DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 \
  -e DATABASE_URL=sqlite:////tmp/smoke.sqlite3 \
  -e DJANGO_SECURE_SSL_REDIRECT=False \
  abj-portal

./deploy/smoke.sh http://127.0.0.1:8011
docker rm -f portal-smoke
```

`deploy/smoke.sh` is the same script CI runs against the built container and the
release workflow runs against production, so all three check the same things. It
waits for the app to answer, then asserts the routes that are easy to break: the
SPA shell and a client-side route both reaching `index.html`, an unknown `/api/`
path still returning `404` rather than HTML, and `/admin/` staying reachable.

`DJANGO_SECURE_SSL_REDIRECT` exists for exactly this and nothing else — leave it
alone on a real deployment.
