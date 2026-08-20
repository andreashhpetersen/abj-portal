# Operations

Running the portal on a real server: how a release happens, how to set the
server up from nothing, and the interim arrangements that are still in force.

Three documents, deliberately separate:

* **`README.md`** — what the portal is and how to develop it.
* **`INFRASTRUCTURE.md`** — *why* the hosting is what it is, what was rejected,
  and what is still outstanding. Read it before adding a third-party service.
* **This file** — *how* to operate what those two decided.

## Shape

One image holds both halves of the app. Gunicorn serves the API and the admin,
and WhiteNoise serves the SPA bundle from the same process, so the browser sees
a single origin and the session and CSRF cookies behave exactly as they do
behind the Vite proxy in development. Caddy terminates TLS in front of it and
renews certificates by itself, which is why no managed load balancer is needed
at this size. The database is UpCloud's, reached through `DATABASE_URL`.

```
Caddy (TLS, :443) ──> gunicorn ──> UpCloud Managed PostgreSQL
                        │
                        ├─ /api/, /admin/  Django
                        ├─ /static/        WhiteNoise (admin + DRF assets)
                        └─ everything else SPA bundle, index.html fallback
```

The catch-all lives in `config/urls.py` behind the `SERVE_SPA` setting, and
excludes `api/`, `admin/` and `static/` — without that exclusion a missing API
route would answer `200` with HTML instead of `404` with JSON.

| File                          | Purpose                                  |
| ----------------------------- | ---------------------------------------- |
| `deploy/Dockerfile`           | Builds the SPA, then the app image       |
| `deploy/docker-compose.yml`   | The stack as it runs on the server       |
| `deploy/Caddyfile`            | TLS and reverse proxy                    |
| `deploy/backup.sh`            | Nightly encrypted `pg_dump` off-server   |
| `deploy/smoke.sh`             | Asserts a running portal serves properly |
| `.github/workflows/ci.yml`    | Checks on pull requests                  |
| `.github/workflows/deploy.yml`| Test, build, release on push to `main`   |

**`/opt/abj-portal/` on the server is not deployed to.** It holds
`docker-compose.yml`, `Caddyfile`, `backup.sh`, `.env` and `secrets/`, all
placed by hand. The release workflow only pins an image and restarts the stack;
it never copies these. So a change to any of them in this repository has to be
copied across deliberately:

```bash
scp deploy/docker-compose.yml root@SERVER:/opt/abj-portal/
ssh root@SERVER 'cd /opt/abj-portal && docker compose up -d'
```

## Releases

A push to `main` deploys. `deploy.yml` runs the same lint, test and typecheck
jobs CI runs, and only then builds. It pushes the image to ghcr.io tagged with
the commit SHA, pins that tag in the server's `.env`, runs `migrate` against the
new image as a release phase — a failure there aborts with the old container
still serving — brings the stack up, and asserts the running container is on the
image it just pinned. Releases are serialised and never cancelled mid-flight,
since a half-finished deploy can leave migrations applied against the previous
image.

Expect a few seconds of downtime while the container is replaced. Rolling that
to zero needs a second app node and a load balancer, which the launch scope does
not justify.

`main` is protected: pull request required, all three CI jobs must pass, and the
branch must be current before merging. Administrators may bypass it, so the
rules are a default rather than a wall.

Note what that protection means here — **push access to `main` is production
access.** A workflow runs arbitrary commands over the deploy key, so anyone who
can merge can reach the server. That is inherent to automated deployment, not to
this setup.

### The deploy credentials

Four repository secrets, plus one variable:

| Name                 | What                                                     |
| -------------------- | -------------------------------------------------------- |
| `DEPLOY_HOST`        | Server address                                            |
| `DEPLOY_USER`        | SSH user — `root`, see below                              |
| `DEPLOY_SSH_KEY`     | Passphraseless ed25519 private key, used only by CI       |
| `DEPLOY_KNOWN_HOSTS` | `known_hosts` lines pinning the server's host key         |
| `PORTAL_DOMAIN` (var)| Public hostname; also switches release verification on    |

The key is dedicated to CI and exists nowhere else — the private half is in the
GitHub secret, the public half in the server's `authorized_keys`. There is no
copy to lose: if it is ever compromised, delete the line from `authorized_keys`,
generate a new pair and set the secret again.

`DEPLOY_KNOWN_HOSTS` should come from a `known_hosts` entry you have already
verified rather than a fresh `ssh-keyscan`, which trusts whatever answers.

`DEPLOY_USER=root` is honest rather than ideal. A dedicated `deploy` user would
need the `docker` group, and docker-group access is root-equivalent — it can
mount the host filesystem into a container — so it would be a boundary in
appearance only.

### Releasing by hand

The fallback when the workflow cannot run. This is exactly what its SSH step
does:

```bash
cd /opt/abj-portal
sed -i '/^PORTAL_IMAGE=/d' .env
echo 'PORTAL_IMAGE=ghcr.io/OWNER/REPO:SHA' >> .env
docker compose pull app
docker compose run --rm -T app python manage.py migrate --noinput </dev/null
docker compose up -d
```

`-T </dev/null` matters whenever the commands arrive on stdin — over
`ssh host 'bash -s' <<EOF`, say. `docker compose run` attaches stdin by default
and will otherwise eat the rest of the script, skipping everything after it
while still exiting successfully. That failure once produced three green deploys
in a row that pulled an image and never restarted the container.

### Rolling back

Superseded images are kept on the server for three days, so a bad release can be
undone without waiting for a build:

```bash
cd /opt/abj-portal
docker image ls --filter label=app=abj-portal   # find the previous SHA tag
sed -i '/^PORTAL_IMAGE=/d' .env
echo 'PORTAL_IMAGE=ghcr.io/OWNER/REPO:PREVIOUS_SHA' >> .env
docker compose up -d
```

That reverts the code, not the database. A release whose migration cannot be
undone has to be fixed forward.

## First-time server setup

Kept as a record of how the current server was built, and what to repeat if one
is ever rebuilt.

1. Create the cloud server and the Managed PostgreSQL instance, and point the
   portal's DNS record at the server.
2. Install Docker, plus `age` and `rclone` for backups.
3. `docker login ghcr.io` with a token carrying `read:packages`, so the server
   can pull the image.
4. Create `/opt/abj-portal/` holding `docker-compose.yml`, `Caddyfile`,
   `backup.sh` and a `.env` built from `.env.example`.
5. Schedule the backup: `17 3 * * *  /opt/abj-portal/backup.sh`, with cron mail
   going somewhere a person reads.
6. Add the repository secrets and variable above, and the CI public key to
   `authorized_keys`. A push to `main` then performs a real deploy.

**Restore-test the backups quarterly.** The database runs on a single node with
three days of point-in-time recovery, and the nightly dump is what covers
anything older. An untested dump is not a backup — `deploy/backup.sh` documents
the restore.

## The shop-rental sync

`manage.py sync_applications` reads the erhvervslejemål form's responses sheet
and reconciles it with the database. It is safe to run at any moment and any
number of times; see `README.md` for what it does and why it polls.

### Google service account

Needed when setting up a new server, or rotating the key.

1. In Google Cloud, create a project and enable the **Google Sheets API**.
2. Create a **service account** — no roles; project roles are irrelevant here.
   Download a JSON key.
3. Share the form's responses spreadsheet, **read-only, with the service
   account's email address**. That share is the only thing granting access, and
   it reaches no other file in the Drive.

### Installing the key

The container runs as **uid 10001**, so the key must be readable by that uid and
the directory traversable. Piping it in avoids the key ever resting in a home
directory, and `umask 077` means it is never on disk at a loose mode:

```bash
ssh root@SERVER 'umask 077 \
  && mkdir -p /opt/abj-portal/secrets \
  && chmod 755 /opt/abj-portal/secrets \
  && cat > /opt/abj-portal/secrets/google-sheets.json \
  && chmod 400 /opt/abj-portal/secrets/google-sheets.json \
  && chown 10001 /opt/abj-portal/secrets/google-sheets.json \
  && ls -ln /opt/abj-portal/secrets/' \
  < path/to/google-sheets.json
```

Expect `-r-------- 1 10001` — numeric, because `ls -ln` skips the name lookup
and 10001 has no name on the host.

Three traps, each of which looked like a working instruction until it met the
actual server:

* **`chown`, not `install -o`.** The user is `portal` *inside the image* and
  does not exist on the host; some `install` builds reject a numeric owner
  outright with `invalid user: '10001'`.
* **`cat`, not `install /dev/stdin`.** Older `install` builds refuse a
  non-regular source, and the failure is easy to miss mid-chain.
* **No group.** The mode is owner-only, and the image's `useradd` passes no
  `--gid`, so the group id is *not* 10001.

### Configuration and the timer

In `/opt/abj-portal/.env` — note the path is the one *inside* the container,
which `docker-compose.yml` mounts from `/opt/abj-portal/secrets/`:

```
SHOPRENTALS_SHEET_ID=<the spreadsheet id from its URL>
SHOPRENTALS_GOOGLE_CREDENTIALS=/run/secrets/abj/google-sheets.json
SHOPRENTALS_SHEET_RANGE=A:ZZ   # optional; the default reads the first sheet
```

All three default to empty, so a checkout with no Google setup still boots and
tests — the command fails with a clear message instead. The mount is a directory
rather than the file for the same reason: the stack starts whether or not a key
is there. `SHOPRENTALS_SHEET_RANGE` carries no sheet name on purpose, so "the
first sheet" survives Google naming the tab by the form's locale.

Check it before scheduling anything:

```bash
docker compose run --rm --no-deps app \
    python manage.py sync_applications --dry-run
```

That reads the sheet, reports how many responses it parsed, warns about anything
it could not map, and writes nothing. Read those warnings: a reworded question
still stores its answer but leaves the mapped column blank, and the fix is an
entry in `HEADER_ALIASES` in `apps/shoprentals/ingest.py`.

Then the timer — every five minutes is ample for applications that take weeks to
become contracts:

```ini
# /etc/systemd/system/abj-sync-applications.service
[Unit]
Description=Sync erhvervslejemål applications from Google Forms
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/abj-portal
ExecStart=/usr/bin/docker compose run --rm --no-deps app \
    python manage.py sync_applications
```

```ini
# /etc/systemd/system/abj-sync-applications.timer
[Unit]
Description=Sync erhvervslejemål applications every five minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

`systemctl daemon-reload && systemctl enable --now abj-sync-applications.timer`.
Compose reads `/opt/abj-portal/.env` itself, so the unit needs no
`EnvironmentFile`; `--no-deps` keeps a sync from dragging Caddy up with it, and
`run` rather than `exec` means it still works when the app container is down.

Watch it with `journalctl -u abj-sync-applications.service -f`. A failed run
needs no intervention — the next one re-reads the whole sheet.

## Interim: running before DNS is ready

**Still in force.** Delete this section once the domain resolves.

Caddy asks Let's Encrypt for a certificate the moment it boots with a domain in
its site address, and Let's Encrypt rate-limits repeated failed challenges — so
if the DNS record does not exist yet, do not point Caddy at the domain and hope.
Serve plain HTTP against the server's IP instead. That exercises the registry
pull, `migrate`, the compose stack and the whole routing contract, leaving only
TLS untested, so when DNS lands there is one variable left rather than five.

In `/opt/abj-portal/.env`:

```
DJANGO_ALLOWED_HOSTS=<server ip>
DJANGO_SECURE_SSL_REDIRECT=False
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

**Logging in will not work over plain HTTP.** `prod.py` sets
`SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE`, neither of which is
env-overridable, so the browser withholds both cookies. `smoke.sh` does not
authenticate, so its checks still pass — expected, not a fault to chase. The
release workflow's verification is skipped too, since it only runs when the
`PORTAL_DOMAIN` variable is set and there is no name to reach the site by.

When DNS is ready, undo it in this order:

1. Delete `DJANGO_SECURE_SSL_REDIRECT=False` from `.env`. Leaving it behind is a
   genuine security regression, not just untidiness.
2. Set `DJANGO_ALLOWED_HOSTS` to the domain.
3. Re-copy `Caddyfile` from the repository rather than editing it back, so the
   server's copy cannot quietly drift.
4. `docker compose up -d`, then `./deploy/smoke.sh https://DOMAIN`.
5. Set the `PORTAL_DOMAIN` repository variable, which turns the release
   workflow's verify step back on.

## Smoke-testing the image locally

Exercises the single-origin arrangement and the SPA fallback that `runserver`
never sees. Port 8011, so it does not collide with a dev server:

```bash
docker build -f deploy/Dockerfile -t abj-portal .
docker run -d --name portal-smoke -p 8011:8000 \
  -e DJANGO_SECRET_KEY=local-smoke-test \
  -e DJANGO_ALLOWED_HOSTS=localhost \
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
path still returning `404` rather than HTML, `/admin/` staying reachable, and
the shop-rental routes answering `403` rather than `200` or `404`.

`DJANGO_ALLOWED_HOSTS` deliberately omits `127.0.0.1` — `prod.py` appends the
loopback itself so the container's `HEALTHCHECK`, which reaches gunicorn
directly, is not rejected as a disallowed host. Listing it here would hide a
container that is unhealthy in every real deployment.

`DJANGO_SECURE_SSL_REDIRECT` exists for exactly this and nothing else — leave it
alone on a real deployment.
