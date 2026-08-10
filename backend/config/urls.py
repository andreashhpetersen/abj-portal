"""
Root URL configuration.

Every feature app owns a `urls.py` and is mounted under /api/ here, so adding a
feature never means editing view code in this file.
"""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    """Liveness probe for the platform's health checks."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/bookings/", include("apps.bookings.urls")),
    path("api/shop-rentals/", include("apps.shoprentals.urls")),
]
