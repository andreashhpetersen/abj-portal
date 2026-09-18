from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

app_name = "bookings"

router = DefaultRouter()
router.register("events", views.EventViewSet, basename="event")
router.register("series", views.EventSeriesViewSet, basename="series")

urlpatterns = [
    path("settings/", views.BookingSettingsView.as_view(), name="settings"),
    path("organizers/", views.EventOrganizerCandidatesView.as_view(), name="organizers"),
    path("", include(router.urls)),
]
