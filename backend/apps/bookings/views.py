"""
The booking API.

Who may do what:

* any logged-in user may see the calendar and book the room privately
* a public booking, and booking one in someone else's name, are further
  gated by `EventSerializer`/`EventSeriesSerializer` — see
  `BookingSettings.public_bookings_open`
* the person who booked, and admins, may edit or cancel a booking
* only admins may delete one outright — everyone else cancels, which keeps the
  record
* only admins may change the booking policy
"""

import datetime as dt

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Exists, OuterRef
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView, RetrieveUpdateAPIView
from rest_framework.response import Response

from apps.accounts.permissions import IsEventOrganizer, IsOwnerOrAdmin
from apps.accounts.serializers import ContactSerializer

from .models import BookingSettings, Event, EventAttendance, EventSeries
from .serializers import (
    AttendanceSerializer,
    BookingSettingsSerializer,
    EventSerializer,
    EventSeriesSerializer,
    EventSeriesUpdateSerializer,
)


class EventViewSet(viewsets.ModelViewSet):
    """The calendar, and everything you can do to a single booking."""

    serializer_class = EventSerializer
    # The calendar is bounded by its date window rather than by page size —
    # paginating it would silently hide bookings from a month view.
    pagination_class = None

    def get_permissions(self):
        if self.action == "destroy":
            return [permissions.IsAuthenticated(), permissions.IsAdminUser()]
        if self.action in {"update", "partial_update", "cancel"}:
            return [permissions.IsAuthenticated(), IsOwnerOrAdmin()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        events = (
            Event.objects.select_related("created_by", "series")
            .annotate(
                _attendee_count=Count("attendances", distinct=True),
                _is_attending=Exists(
                    EventAttendance.objects.filter(event=OuterRef("pk"), user=user.pk)
                ),
            )
            .order_by("start")
        )

        # Filters shape the calendar only. A detail route must be able to reach
        # any booking — otherwise cancelling one would hide it from the very
        # request that wants to look at, delete, or reinstate it.
        if self.action != "list":
            return events

        params = self.request.query_params
        if params.get("include_cancelled", "").lower() not in {"1", "true", "yes"}:
            events = events.filter(cancelled_at__isnull=True)
        if category := params.get("category"):
            events = events.filter(category=category)

        # `from`/`to` are inclusive local dates, and an event matches when it
        # overlaps the window at all, not only when it starts inside it.
        if window_start := self._as_datetime(params.get("from"), "from"):
            events = events.filter(end__gt=window_start)
        if window_end := self._as_datetime(params.get("to"), "to", end_of_day=True):
            events = events.filter(start__lt=window_end)
        return events

    def _as_datetime(self, value, field, end_of_day=False):
        """Turn an inclusive local date into a timezone-aware boundary."""
        if not value:
            return None
        day = parse_date(value)
        if day is None:
            raise ValidationError({field: "Use the date format YYYY-MM-DD."})
        if end_of_day:
            day += dt.timedelta(days=1)  # exclusive upper bound at midnight
        return timezone.make_aware(
            dt.datetime.combine(day, dt.time.min), timezone.get_current_timezone()
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Soft-cancel. The booking stays on the record and frees its slot."""
        event = self.get_object()
        if event.is_cancelled:
            raise ValidationError({"detail": "The booking is already cancelled."})
        event.cancel(by=request.user)
        return Response(self.get_serializer(event).data)

    @action(detail=True, methods=["post", "delete"], url_path="attendance")
    def attendance(self, request, pk=None):
        """Sign up for a public event, or withdraw again."""
        event = self.get_object()

        if request.method == "DELETE":
            EventAttendance.objects.filter(event=event, user=request.user).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = AttendanceSerializer(data={}, context={"event": event})
        serializer.is_valid(raise_exception=True)
        try:
            EventAttendance.objects.create(event=event, user=request.user)
        except DjangoValidationError as error:
            # Signing up twice trips the uniqueness constraint.
            raise ValidationError(error.message_dict) from error
        return Response(self.get_serializer(self.get_object()).data, status=status.HTTP_201_CREATED)


class EventSeriesViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Recurring public events.

    A series can be renamed, retimed and made to run longer or stop sooner. Its
    *rhythm* cannot be changed — see `EventSeriesUpdateSerializer` — so changing
    how often it meets is still delete and recreate.
    """

    queryset = EventSeries.objects.select_related("created_by").prefetch_related(
        "occurrences__created_by"
    )

    def get_serializer_class(self):
        if self.action in {"update", "partial_update"}:
            return EventSeriesUpdateSerializer
        return EventSeriesSerializer

    def get_permissions(self):
        if self.action == "destroy":
            return [permissions.IsAuthenticated(), permissions.IsAdminUser()]
        if self.action in {"update", "partial_update"}:
            return [permissions.IsAuthenticated(), IsOwnerOrAdmin()]
        return [permissions.IsAuthenticated()]


class BookingSettingsView(RetrieveUpdateAPIView):
    """The association's booking policy.

    Readable by everyone so the SPA can hide the private-booking form when it is
    closed, writable only by admins.
    """

    serializer_class = BookingSettingsSerializer

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), permissions.IsAdminUser()]

    def get_object(self):
        return BookingSettings.load()


class EventOrganizerCandidatesView(ListAPIView):
    """Who a privileged booker may name as the organizer of a public event.

    Gated on `IsEventOrganizer` itself, not merely `IsAuthenticated`: this is a
    wider list than shoprentals' committee-members dropdown — anyone active,
    resident or not, since the organizer is simply who ends up able to edit and
    is shown as the contact, not a committee role.
    """

    serializer_class = ContactSerializer
    permission_classes = [permissions.IsAuthenticated, IsEventOrganizer]
    pagination_class = None

    def get_queryset(self):
        return (
            get_user_model()
            .objects.filter(is_active=True)
            .order_by("first_name", "last_name", "email")
        )
