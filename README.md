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
  apps/accounts/     Custom email-based User, auth endpoints, shared permissions
  apps/bookings/     Feature 1 — community room. Placeholder.
  apps/shoprentals/  Feature 2 — shop rentals. Placeholder.
frontend/
  src/api/           fetch wrapper (cookies + CSRF)
  src/auth/          AuthContext — holds the logged-in member
  src/pages/         One component per route
```

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
