"""
Settings shared by every environment.

Environment-specific modules (`dev.py`, `prod.py`) import everything from here
and override what differs. Nothing in this file may read a secret without a
default, so that `manage.py check` works on a bare checkout.
"""

from pathlib import Path

import environ

# BASE_DIR points at `backend/`, i.e. the directory containing manage.py.
BASE_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = BASE_DIR.parent

env = environ.Env()
# Read repo-root .env if present; real deployments use real environment variables.
environ.Env.read_env(REPO_ROOT / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "corsheaders",
    "django_filters",
]

LOCAL_APPS = [
    "apps.accounts",
    "apps.bookings",
    "apps.shoprentals",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# SQLite by default so a fresh checkout runs with no services; DATABASE_URL
# (e.g. postgres://...) overrides it in every real environment.
DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "da-dk"
TIME_ZONE = "Europe/Copenhagen"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Where `npm run build` puts the SPA. In development the Vite dev server owns
# these files and Django never sees them; in production one process serves both,
# so there is a single origin and no CORS exemption. SERVE_SPA switches that on
# — set it locally too if you want to check the real production bundle.
SPA_DIST = REPO_ROOT / "frontend" / "dist"
SERVE_SPA = env.bool("DJANGO_SERVE_SPA", default=False)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Outgoing mail. The portal sends booking confirmations from its own subdomain
# through a transactional provider, kept separate from the association's human
# mailboxes so that automated sending cannot damage their reputation — see
# INFRASTRUCTURE.md. Development overrides the backend to write to the console.
EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("DJANGO_EMAIL_HOST", default="")
EMAIL_PORT = env.int("DJANGO_EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("DJANGO_EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("DJANGO_EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("DJANGO_EMAIL_USE_TLS", default=True)
EMAIL_TIMEOUT = env.int("DJANGO_EMAIL_TIMEOUT", default=10)
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", default="beboerportal@localhost")
# Error mail to the admins uses this instead of DEFAULT_FROM_EMAIL.
SERVER_EMAIL = env("DJANGO_SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)

# Shop-rental applications are ingested from the public Google Form's responses
# sheet by `manage.py sync_applications`. All three have empty defaults so a
# checkout with no Google setup still boots and tests — the command fails with a
# clear message instead, and nothing else in the portal touches Google.
# The sheet is shared read-only with the service account named in the key file.
SHOPRENTALS_SHEET_ID = env("SHOPRENTALS_SHEET_ID", default="")
SHOPRENTALS_GOOGLE_CREDENTIALS = env("SHOPRENTALS_GOOGLE_CREDENTIALS", default="")
# A1 notation without a sheet name means "the first sheet", which avoids
# depending on whether Google named the tab "Formularsvar 1" or "Form Responses 1".
SHOPRENTALS_SHEET_RANGE = env("SHOPRENTALS_SHEET_RANGE", default="A:ZZ")

# The SPA authenticates with the session cookie it already has, so DRF only
# needs session auth. Everything is private by default; endpoints that should
# be public opt out explicitly with their own permission_classes.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    # Signup is the only endpoint an unauthenticated visitor can write through,
    # so it is the only one that needs a rate. Per IP, and tight on purpose: a
    # resident signs up once in their life, and a whole household sharing one
    # router is still only a handful. Note the effective limit is this times the
    # number of gunicorn workers (3 in deploy/Dockerfile), because there is no
    # CACHES setting and LocMemCache is per-process — enough to stop a flood,
    # not a precise quota. Configure a shared cache if that ever matters.
    # Same reasoning for password reset: nobody legitimately requests it often,
    # and it is the second endpoint (after signup) that an anonymous visitor
    # can use to fish for whether an email belongs to an account here — the
    # uniform response is what mainly guards against that, but the throttle
    # keeps a script from just trying many addresses quickly. The confirm step
    # gets its own scope because it is reached from an emailed link rather
    # than typed by hand, so a much higher rate does not cost anything.
    "DEFAULT_THROTTLE_RATES": {
        "signup": "10/hour",
        "password_reset": "10/hour",
        "password_reset_confirm": "20/hour",
    },
}
