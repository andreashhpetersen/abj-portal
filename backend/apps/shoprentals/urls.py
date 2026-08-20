from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

app_name = "shoprentals"

router = DefaultRouter()
router.register("applications", views.ApplicationViewSet, basename="application")

urlpatterns = [
    # Before the router, so "members" is not read as an application id.
    path("members/", views.CommitteeMembersView.as_view(), name="members"),
    path(
        "applications/<int:pk>/details/",
        views.ApplicationDetailsView.as_view(),
        name="application-details",
    ),
    path("comments/<int:pk>/", views.CommentDeleteView.as_view(), name="comment-detail"),
    path("", include(router.urls)),
]
