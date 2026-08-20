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

## Shop-rental applications (erhvervslejemål)

Candidates apply through a public **Google Form**. The portal reads the form's
responses sheet on a timer and gives the erhvervsudvalg somewhere to work on what
arrives: a status, a 1–5 rating, comments, an assignee, and a place to collect
the concrete facts a lease needs.

Nobody outside the erhvervsudvalg can see any of it.

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

    python manage.py sync_applications
    python manage.py sync_applications --dry-run   # parse and report, write nothing

**Polling, not a webhook, and reconciling rather than replaying.** Every run
reads the whole sheet and upserts. A failed run, a deploy mid-run, a fortnight of
downtime or a cell corrected by hand in the sheet all sort themselves out on the
next pass — which is worth much more here than seconds-fresh delivery, given that
these applications take weeks to become contracts.

**The form is expected to change, so the schema does not mirror it.** Every
answer is stored verbatim and in order, and the UI renders whatever arrives. Only
the three fields the committee filters and searches on — name, email, phone — are
lifted into columns, by the alias table `HEADER_ALIASES` in `ingest.py`. That
table is the *only* place a form change needs reflecting: a brand-new question
costs nothing at all, and a reworded contact question still stores its answer,
leaves the column blank, and makes the sync command say so on stderr.

**A sync never touches the committee's work.** It writes only the applicant's own
fields. Status, rating, assignee, comments and contract details are not ours to
move, and a sync that reset a rating would be one nobody could safely run. Rows
that vanish from the sheet keep their applications, which by then may carry
months of notes.

Identity comes from a digest of the submission's timestamp and email, because a
responses sheet has no id of its own and a row number would re-point every key
below it the moment someone sorted the sheet. The consequence: correct an
applicant's email *in the portal*, not in the sheet — changing it upstream files
the next sync as a new application.

### Setting up the Google side

1. In Google Cloud, create a project and enable the **Google Sheets API**.
2. Create a **service account**, no roles needed, and download a JSON key.
3. Open the form's responses spreadsheet and **share it, read-only, with the
   service account's email address** — that share is the only thing granting
   access, and it reaches no other file in the Drive.
4. **Get the new build and the new compose file onto the server first.** Two
   things the earlier steps do not do for you:

   * The image must contain `google-api-python-client`, which means deploying a
     build from after this feature landed — push to `main` and let the release
     workflow run.
   * `docker-compose.yml` gained the `./secrets` mount, and **the deploy workflow
     does not copy it**. It only sets `PORTAL_IMAGE`, pulls and restarts, so
     `/opt/abj-portal/docker-compose.yml` is whatever was placed there by hand
     during *First-time server setup*. Copy the new one over and recreate the
     container, or the key will not exist inside it:

         scp deploy/docker-compose.yml <user>@<host>:/opt/abj-portal/
         ssh <user>@<host> 'cd /opt/abj-portal && docker compose up -d'

   The same applies to any later change to `docker-compose.yml`, `Caddyfile` or
   `backup.sh`.
5. Put the key on the server at `/opt/abj-portal/secrets/google-sheets.json`.
   `docker-compose.yml` bind-mounts that directory read-only at
   `/run/secrets/abj`, so the path the app sees is not the path on the host.
   The container runs as uid 10001, so make the key readable by it. Set the
   owner with `chown` rather than `install -o`: the user is called `portal`
   inside the image and does not exist on the host at all, and some builds of
   `install` reject a numeric `-o` outright (`invalid user: '10001'`) where
   `chown` takes uids happily. The mode is 0400, owner-only, so the *group* does
   not matter — which is just as well, since the image's `useradd` passes no
   `--gid` and the group id is therefore not 10001.

       sudo install -d -m 755 /opt/abj-portal/secrets
       sudo install -m 400 ~/google-sheets.json \
            /opt/abj-portal/secrets/google-sheets.json
       sudo chown 10001 /opt/abj-portal/secrets/google-sheets.json
       ls -ln /opt/abj-portal/secrets/     # expect: -r-------- 1 10001 ...

   `install` writes it 0400 from the start, so the key never sits on disk
   world-readable between being copied and being locked down. Delete the copy in
   your home directory afterwards — `shred -u ~/google-sheets.json`.

   To avoid staging it in a home directory at all, stream it straight in when
   logging in as root (no `sudo`, which would fight the key for stdin):

       ssh root@HOST 'set -e
       install -d -m 755 /opt/abj-portal/secrets
       install -m 400 /dev/stdin /opt/abj-portal/secrets/google-sheets.json
       chown 10001 /opt/abj-portal/secrets/google-sheets.json' \
         < path/to/google-sheets.json

   Then in `/opt/abj-portal/.env`:

       SHOPRENTALS_SHEET_ID=<the spreadsheet id from its URL>
       SHOPRENTALS_GOOGLE_CREDENTIALS=/run/secrets/abj/google-sheets.json
       SHOPRENTALS_SHEET_RANGE=A:ZZ   # optional; the default reads the first sheet

   All three default to empty, so a checkout with no Google setup still boots and
   tests — `sync_applications` fails with a clear message instead. The mount is a
   directory rather than the file itself for the same reason: the stack starts
   whether or not a key is there.

   Locally, point `SHOPRENTALS_GOOGLE_CREDENTIALS` straight at wherever you
   saved the key — there is no container in the way.
6. Check it end to end before scheduling anything:

       docker compose run --rm --no-deps app \
           python manage.py sync_applications --dry-run

   That reads the sheet, reports how many responses it parsed and warns about
   anything it could not map, and writes nothing. Drop `--dry-run` once it looks
   right.
7. Run it on a timer. Every five minutes is plenty:

       # /etc/systemd/system/abj-sync-applications.service
       [Unit]
       Description=Sync erhvervslejemål applications from Google Forms
       After=docker.service
       [Service]
       Type=oneshot
       WorkingDirectory=/opt/abj-portal
       ExecStart=/usr/bin/docker compose run --rm --no-deps app \
           python manage.py sync_applications

       # /etc/systemd/system/abj-sync-applications.timer
       [Unit]
       Description=Sync erhvervslejemål applications every five minutes
       [Timer]
       OnBootSec=5min
       OnUnitActiveSec=5min
       [Install]
       WantedBy=timers.target

   Then `sudo systemctl enable --now abj-sync-applications.timer`. Compose reads
   `/opt/abj-portal/.env` itself, so the unit needs no `EnvironmentFile`;
   `--no-deps` keeps a sync from dragging Caddy up with it, and `run` rather than
   `exec` means the sync still works when the app container is down.

   Watch it with `journalctl -u abj-sync-applications.service -f`. A failed run
   needs no intervention — the next one reads the whole sheet again.

`SHOPRENTALS_SHEET_RANGE` deliberately carries no sheet name: Google names the
responses tab by the form's locale, so "the first sheet" survives both
*Formularsvar 1* and *Form Responses 1*.

For local development, `python manage.py seed_demo` creates six applications
through the real ingest path, so the page has something to show without any of
the above.

### Preparing the contract

Each application has a *Kontraktoplysninger* record: CVR, legal form, contact
person, which unit, what it may be used for, area, rent, deposit, wanted
handover, and free-text notes. None of it comes from the form — the form asks
only enough to judge whether someone is worth talking to, and the rest arrives
over weeks of correspondence. So every field is optional and a half-filled record
is the normal state, saved a field at a time.

The API reports `missing_fields`, the labels of what is still outstanding, which
the UI shows as a checklist. **Generating the document for the lawyer is not
built** — the record is shaped so that it is a formatting job, but there is no
export yet, and the field list is a starting point that should be reconciled with
what the lawyer actually asks for.

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
nothing else: the applicant's answers are a record of what was submitted, and the
sync would overwrite an edit anyway. There is no `POST` and no `DELETE` —
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
only `sync_applications` complains. See *Shop-rental applications* above.

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

**Rolling back.** Superseded images are kept on the server for three days, so a
bad release can be undone without waiting for a build. On the server:

```bash
cd /opt/abj-portal
docker image ls --filter label=app=abj-portal   # find the previous SHA tag
sed -i '/^PORTAL_IMAGE=/d' .env
echo "PORTAL_IMAGE=ghcr.io/<owner>/<repo>:<previous-sha>" >> .env
docker compose up -d
```

That reverts the code, not the database. A release whose migration cannot be
undone has to be fixed forward.

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

### Bringing the server up before DNS is ready

Caddy asks Let's Encrypt for a certificate the moment it boots with a domain in
its site address, and Let's Encrypt rate-limits repeated failed challenges — so
if the DNS record does not exist yet, do not point Caddy at the domain and hope.
Serve plain HTTP against the server's IP instead. That exercises the registry
pull, `migrate`, the compose stack and the whole routing contract, leaving only
TLS untested, so when DNS lands there is one variable left rather than five.

In `/opt/abj-portal/.env` on the server:

```
DJANGO_ALLOWED_HOSTS=<server ip>
DJANGO_SECURE_SSL_REDIRECT=False
PORTAL_IMAGE=ghcr.io/<owner>/<repo>:latest
```

Keep `PORTAL_DOMAIN` and `ACME_EMAIL` set even though Caddy will not read the
domain in this mode: `docker-compose.yml` marks both required and refuses to
start without them.

In `/opt/abj-portal/Caddyfile`, replace the site address with a bare port:

```
:80 {
```

With no hostname there, Caddy skips automatic HTTPS altogether, so nothing is
requested from Let's Encrypt and no rate limit is spent.

Then, on the server:

```bash
cd /opt/abj-portal
docker compose run --rm app python manage.py migrate
docker compose up -d
```

And from a workstation, against the server's IP:

```bash
./deploy/smoke.sh http://SERVER_IP
```

**Logging in will not work over plain HTTP.** `prod.py` sets
`SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE`, neither of which is
env-overridable, so the browser withholds both cookies. `smoke.sh` does not
authenticate, so its checks still pass — this is expected, not a fault to chase.

When DNS is ready, undo it in this order:

1. Delete `DJANGO_SECURE_SSL_REDIRECT=False` from `.env`. Leaving it behind is a
   genuine security regression, not just untidiness
2. Set `DJANGO_ALLOWED_HOSTS` to the domain
3. Re-copy `Caddyfile` from the repository rather than editing it back, so the
   server's copy cannot quietly drift
4. `docker compose up -d`, then `./deploy/smoke.sh https://DOMAIN`

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
