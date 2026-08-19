"""
Root URL configuration.

Every feature app owns a `urls.py` and is mounted under /api/ here, so adding a
feature never means editing view code in this file.
"""

from django.conf import settings
from django.contrib import admin
from django.http import FileResponse, Http404, JsonResponse
from django.urls import include, path, re_path


def health(_request):
    """Liveness probe for the platform's health checks."""
    return JsonResponse({"status": "ok"})


def spa_index(_request):
    """
    Serve the SPA shell for any path the frontend owns.

    The file is streamed rather than rendered as a template: it is Vite's
    output, not ours, and running it through the template engine would give
    meaning to any brace sequence a future plugin decides to emit.

    It must not be cached. index.html names the hashed asset bundles, so a
    stale copy points at files a new deployment has already replaced.
    """
    index = settings.SPA_DIST / "index.html"
    if not index.is_file():
        raise Http404("The frontend has not been built — run `npm run build`.")
    response = FileResponse(index.open("rb"), content_type="text/html")
    response["Cache-Control"] = "no-cache"
    return response


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/bookings/", include("apps.bookings.urls")),
    path("api/shop-rentals/", include("apps.shoprentals.urls")),
]

if settings.SERVE_SPA:
    # Last, and only in deployments that serve the bundle: anything not claimed
    # above belongs to the frontend's router. The lookahead keeps Django's own
    # prefixes out — without it the SPA would swallow the admin, and a genuinely
    # missing API route would answer 200 with HTML instead of 404 with JSON.
    urlpatterns += [
        re_path(r"^(?!api/|admin/|static/).*$", spa_index, name="spa"),
    ]
