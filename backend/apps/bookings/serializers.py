"""
Serializers for the booking API.

The model is the authority on the booking rules, so these do not restate them.
What they must do is run `full_clean()` during validation and translate Django's
`ValidationError` into DRF's, otherwise `Event.save()` would raise mid-write and
DRF would return a 500 where the client deserves a field-level 400.
"""

import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from apps.accounts.serializers import ContactSerializer

from .models import BookingSettings, Event, EventCategory, EventSeries


def run_model_validation(instance):
    """Validate a model instance, reporting failures the way DRF does."""
    try:
        instance.full_clean()
    except DjangoValidationError as error:
        raise serializers.ValidationError(error.message_dict) from error


class EventSerializer(serializers.ModelSerializer):
    created_by = ContactSerializer(read_only=True)
    is_cancelled = serializers.BooleanField(read_only=True)
    attendee_count = serializers.SerializerMethodField()
    is_attending = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            "id",
            "category",
            "title",
            "description",
            "start",
            "end",
            "created_by",
            "series",
            "is_cancelled",
            "cancelled_at",
            "attendee_count",
            "is_attending",
            "can_cancel",
        ]
        read_only_fields = ["id", "created_by", "series", "cancelled_at"]

    def get_attendee_count(self, obj) -> int:
        annotated = getattr(obj, "_attendee_count", None)
        return annotated if annotated is not None else obj.attendances.count()

    def get_is_attending(self, obj) -> bool:
        annotated = getattr(obj, "_is_attending", None)
        if annotated is not None:
            return annotated
        user = self.context["request"].user
        return obj.attendances.filter(user=user).exists()

    def get_can_cancel(self, obj) -> bool:
        """Whether the requesting user may cancel this booking."""
        user = self.context["request"].user
        return not obj.is_cancelled and (user.is_staff or obj.created_by_id == user.pk)

    def validate(self, attrs):
        # Build the instance the write would produce and validate that, so the
        # model's rules — overlap, horizon, title — become 400s with field names.
        if self.instance is None:
            candidate = Event(**attrs, created_by=self.context["request"].user)
        else:
            candidate = copy.deepcopy(self.instance)
            for field, value in attrs.items():
                setattr(candidate, field, value)
        run_model_validation(candidate)
        return attrs


class EventSeriesSerializer(serializers.ModelSerializer):
    """Creating a series also creates its occurrences, in one transaction."""

    created_by = ContactSerializer(read_only=True)
    start = serializers.DateTimeField(write_only=True)
    end = serializers.DateTimeField(write_only=True)
    occurrences = EventSerializer(many=True, read_only=True)

    class Meta:
        model = EventSeries
        fields = [
            "id",
            "title",
            "description",
            "frequency",
            "interval",
            "until",
            "created_by",
            "created_at",
            "start",
            "end",
            "occurrences",
        ]
        read_only_fields = ["id", "created_by", "created_at", "occurrences"]

    def validate(self, attrs):
        if attrs["end"] <= attrs["start"]:
            raise serializers.ValidationError({"end": "The event must end after it starts."})
        if attrs["until"] < attrs["start"].date():
            raise serializers.ValidationError({"until": "The series ends before it begins."})
        return attrs

    def create(self, validated_data):
        start = validated_data.pop("start")
        end = validated_data.pop("end")

        # Atomic so a clash partway through leaves nothing behind: a series with
        # half its evenings booked is worse than no series at all.
        with transaction.atomic():
            series = EventSeries.objects.create(
                created_by=self.context["request"].user, **validated_data
            )
            try:
                series.create_occurrences(start, end)
            except DjangoValidationError as error:
                raise serializers.ValidationError(
                    {
                        "start": [
                            "One of the occurrences clashes with an existing booking: "
                            f"{'; '.join(sum(error.message_dict.values(), []))}"
                        ]
                    }
                ) from error
        return series


class BookingSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingSettings
        fields = [
            "private_bookings_enabled",
            "private_booking_min_notice_days",
            "private_booking_max_horizon_days",
            "private_booking_weekdays",
        ]

    def validate(self, attrs):
        candidate = copy.deepcopy(self.instance) if self.instance else BookingSettings()
        for field, value in attrs.items():
            setattr(candidate, field, value)
        run_model_validation(candidate)
        return attrs


class AttendanceSerializer(serializers.Serializer):
    """Attendance carries no payload — the event and the user are both known."""

    def validate(self, attrs):
        event = self.context["event"]
        if event.category != EventCategory.PUBLIC:
            raise serializers.ValidationError({"event": "Only public events take attendance."})
        if event.is_cancelled:
            raise serializers.ValidationError({"event": "The event is cancelled."})
        return attrs
