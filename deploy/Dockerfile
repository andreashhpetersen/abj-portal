# One image holds both halves of the portal: gunicorn serves the API and the
# admin, and WhiteNoise serves the SPA bundle built in the first stage. That is
# what keeps the browser on a single origin, so the session and CSRF cookies
# behave in production exactly as they do behind the Vite proxy in development.
#
# Build from the repository root:
#   docker build -f deploy/Dockerfile -t abj-portal .

# ---------------------------------------------------------------- frontend ---
FROM node:24-alpine AS frontend

WORKDIR /build

# Dependencies first, so a source-only change does not reinstall them.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# `npm run build` typechecks before bundling, so a type error fails the image
# build rather than reaching production.
RUN npm run build

# ----------------------------------------------------------------- runtime ---
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

WORKDIR /app

# psycopg is installed as the binary wheel, so no libpq or compiler is needed.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
# Must land at /app/frontend/dist: settings derive SPA_DIST from the repository
# layout, so the image mirrors it.
COPY --from=frontend /build/dist frontend/dist

WORKDIR /app/backend

# Collected at build time so the running container never writes to its own
# filesystem. prod.py demands these three variables at import, and collectstatic
# touches neither the database nor the hostname, so build-only values are safe.
RUN DJANGO_SECRET_KEY=build-time-only \
    DJANGO_ALLOWED_HOSTS=localhost \
    DATABASE_URL=sqlite:////tmp/build.sqlite3 \
    python manage.py collectstatic --noinput \
    && rm -f /tmp/build.sqlite3

RUN useradd --system --create-home --uid 10001 portal \
    && chown -R portal:portal /app
USER portal

EXPOSE 8000

# Reaches gunicorn directly, so it arrives without X-Forwarded-Proto — prod.py
# exempts this one path from the HTTPS redirect for exactly this reason.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/', timeout=4)"

# --forwarded-allow-ips is wide open on purpose: the container publishes no port
# of its own, so the only thing that can reach gunicorn is Caddy on the internal
# network, and gunicorn must trust its X-Forwarded-Proto for SECURE_SSL_REDIRECT
# to see that TLS was already terminated.
CMD ["sh", "-c", "exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers ${GUNICORN_WORKERS:-3} \
    --timeout ${GUNICORN_TIMEOUT:-30} \
    --forwarded-allow-ips='*' \
    --access-logfile - \
    --error-logfile -"]
