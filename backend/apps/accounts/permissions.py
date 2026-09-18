"""Reusable DRF permissions. Feature apps import from here rather than
re-deriving who is allowed to do what."""

from rest_framework import permissions


class IsBusinessCommittee(permissions.BasePermission):
    """Restricts a view to the erhvervsudvalg (and superusers)."""

    message = "Kræver medlemskab af erhvervsudvalget."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_business_committee)


class IsEventOrganizer(permissions.BasePermission):
    """Restricts a view to the beboerlokalegruppe (and superusers)."""

    message = "Kræver medlemskab af beboerlokalegruppen."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_event_organizer)


class IsOwnerOrAdmin(permissions.BasePermission):
    """Object-level write access for the object's creator, or any admin.

    Relies on the object exposing a `created_by` foreign key to the member who
    made it. Reads are left to the view's other permissions.
    """

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return user.is_staff or getattr(obj, "created_by_id", None) == user.pk
