"""
Production settings.

Every value here that matters is required from the environment — a missing
DJANGO_SECRET_KEY or DJANGO_ALLOWED_HOSTS should crash at boot rather than
quietly start an insecure server.
"""

from .base import *  # noqa: F403
from .base import env

DEBUG = False
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

DATABASES = {"default": env.db_url("DATABASE_URL")}

# The SPA is served from the same origin in production, so no CORS exemption is
# granted by default. Set DJANGO_CORS_ALLOWED_ORIGINS only if that changes.
CORS_ALLOWED_ORIGINS = env.list("DJANGO_CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# TLS is terminated by Caddy in front of gunicorn, which forwards the scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Overridable only so the image can be smoke-tested locally over plain HTTP.
# Never turn it off on a real deployment.
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
# The container's own health check talks to gunicorn directly, so it arrives
# without X-Forwarded-Proto. Without this exemption it would be redirected to a
# port nothing is listening on, and the container would be declared unhealthy.
SECURE_REDIRECT_EXEMPT = [r"^api/health/$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MIDDLEWARE.insert(  # noqa: F405
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,  # noqa: F405
    "whitenoise.middleware.WhiteNoiseMiddleware",
)

# One process serves the API, the admin and the SPA, so the browser sees a
# single origin. WhiteNoise serves Django's own collected static files under
# /static/ and the SPA's hashed bundles from the root; the catch-all route in
# config/urls.py answers the frontend's own paths with index.html.
SERVE_SPA = env.bool("DJANGO_SERVE_SPA", default=True)
WHITENOISE_ROOT = SPA_DIST  # noqa: F405

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
