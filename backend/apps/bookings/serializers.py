"""
Serializers for the booking API.

The model is the authority on the booking rules, so these do not restate them.
What they must do is run `full_clean()` during validation and translate Django's
`ValidationError` into DRF's, otherwise `Event.save()` would raise mid-write and
DRF would return a 500 where the client deserves a field-level 400.
"""

import copy
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.accounts.serializers import ContactSerializer

from .models import BookingSettings, Event, EventCategory, EventSeries

User = get_user_model()


def run_model_validation(instance):
    """Validate a model instance, reporting failures the way DRF does."""
    try:
        instance.full_clean()
    except DjangoValidationError as error:
        raise serializers.ValidationError(error.message_dict) from error


class EventSerializer(serializers.ModelSerializer):
    created_by = ContactSerializer(read_only=True)
    # Same trick as shoprentals' `assignee`/`assignee_id`: a different field
    # name so both the read-only nested contact and this write-only id can
    # target `created_by`. Only a privileged booker may actually use it to
    # name someone other than themselves — see `validate()`.
    organizer_id = serializers.PrimaryKeyRelatedField(
        source="created_by",
        queryset=User.objects.filter(is_active=True),
        write_only=True,
        required=False,
    )
    is_cancelled = serializers.BooleanField(read_only=True)
    attendee_count = serializers.SerializerMethodField()
    is_attending = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()

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
            "organizer_id",
            "series",
            "is_cancelled",
            "cancelled_at",
            "attendee_count",
            "is_attending",
            "can_cancel",
            "can_edit",
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

    def get_can_edit(self, obj) -> bool:
        """Whether the requesting user may change this booking.

        The same people who may cancel it, and for the same reason it is asked
        separately: a cancelled booking is a record, not a plan. Editing one
        would not reclaim its slot — `_clashing_events` ignores cancelled
        events — so it would look like a rebooking and be nothing of the kind.
        """
        user = self.context["request"].user
        return not obj.is_cancelled and (user.is_staff or obj.created_by_id == user.pk)

    def validate(self, attrs):
        if self.instance is not None and self.instance.is_cancelled:
            raise serializers.ValidationError(
                {"detail": "En aflyst booking kan ikke ændres. Lav en ny booking i stedet."}
            )

        if self.instance is not None:
            # The organizer is who `can_edit`/`can_cancel` and the contact
            # details resolve against, so reassigning it after the fact is not
            # an edit like the others — it would hand off a booking mid-edit.
            if "created_by" in attrs:
                raise serializers.ValidationError(
                    {"organizer_id": "Arrangøren kan ikke ændres efter oprettelsen."}
                )
            candidate = copy.deepcopy(self.instance)
            for field, value in attrs.items():
                setattr(candidate, field, value)
            run_model_validation(candidate)
            return attrs

        user = self.context["request"].user
        organizer = attrs.get("created_by") or user
        privileged = user.is_staff or user.is_event_organizer

        if organizer != user and not privileged:
            raise serializers.ValidationError(
                {"organizer_id": "Kun arrangementsudvalget kan booke på en andens vegne."}
            )
        if (
            attrs.get("category") == EventCategory.PUBLIC
            and not privileged
            and not BookingSettings.load().public_bookings_open
        ):
            raise serializers.ValidationError(
                {
                    "category": (
                        "Fælles arrangementer kan i øjeblikket kun oprettes af "
                        "arrangementsudvalget."
                    )
                }
            )
        attrs["created_by"] = organizer

        # Build the instance the write would produce and validate that, so the
        # model's rules — overlap, horizon, title — become 400s with field names.
        candidate = Event(**attrs)
        run_model_validation(candidate)
        return attrs


class EventSeriesSerializer(serializers.ModelSerializer):
    """Creating a series also creates its occurrences, in one transaction."""

    created_by = ContactSerializer(read_only=True)
    organizer_id = serializers.PrimaryKeyRelatedField(
        source="created_by",
        queryset=User.objects.filter(is_active=True),
        write_only=True,
        required=False,
    )
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
            "organizer_id",
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

        # A series is always public, so it is governed by the same policy as a
        # one-off public event — see `EventSerializer.validate()`.
        user = self.context["request"].user
        organizer = attrs.get("created_by") or user
        privileged = user.is_staff or user.is_event_organizer
        if organizer != user and not privileged:
            raise serializers.ValidationError(
                {"organizer_id": "Kun arrangementsudvalget kan booke på en andens vegne."}
            )
        if not privileged and not BookingSettings.load().public_bookings_open:
            raise serializers.ValidationError(
                {
                    "detail": (
                        "Gentagne arrangementer kan i øjeblikket kun oprettes af "
                        "arrangementsudvalget."
                    )
                }
            )
        attrs["created_by"] = organizer
        return attrs

    def create(self, validated_data):
        start = validated_data.pop("start")
        end = validated_data.pop("end")

        # Atomic so a clash partway through leaves nothing behind: a series with
        # half its evenings booked is worse than no series at all.
        with transaction.atomic():
            series = EventSeries.objects.create(
                # Remember the pattern's origin. Occurrences can be moved one at
                # a time, so they cannot be trusted to say where it began.
                seed_start=start,
                seed_end=end,
                **validated_data,
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


class EventSeriesUpdateSerializer(serializers.ModelSerializer):
    """Changing a series after the fact.

    What it is called, what time of day it meets, and how long it runs for —
    but never *how often*, because a different rhythm is a different series.
    Re-materialising one would have to decide what becomes of the evenings
    people have already signed up for, and that is a question for the board
    rather than a default. Deleting and recreating remains the way to do it.

    The times move by a delta rather than to an absolute hour: the client knows
    what the occurrence it is editing used to be, and a delta is what leaves an
    evening somebody moved by hand still moved.
    """

    #: Half a day either way. This retimes a series; it does not reschedule it.
    SHIFT_LIMIT_MINUTES = 720

    created_by = ContactSerializer(read_only=True)
    start_shift_minutes = serializers.IntegerField(
        required=False,
        default=0,
        write_only=True,
        min_value=-SHIFT_LIMIT_MINUTES,
        max_value=SHIFT_LIMIT_MINUTES,
    )
    end_shift_minutes = serializers.IntegerField(
        required=False,
        default=0,
        write_only=True,
        min_value=-SHIFT_LIMIT_MINUTES,
        max_value=SHIFT_LIMIT_MINUTES,
    )

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
            "start_shift_minutes",
            "end_shift_minutes",
        ]
        read_only_fields = ["id", "frequency", "interval", "created_by", "created_at"]

    def validate_until(self, value):
        seed_start, _seed_end = self.instance.pattern_seed()
        if seed_start is not None and value < timezone.localdate(seed_start):
            raise serializers.ValidationError("Serien ville slutte, før den begyndte.")
        return value

    def update(self, instance, validated_data):
        start_shift = timedelta(minutes=validated_data.pop("start_shift_minutes", 0))
        end_shift = timedelta(minutes=validated_data.pop("end_shift_minutes", 0))
        was_titled, was_described = instance.title, instance.description

        # All of it or none of it. A clash halfway through would leave the
        # series meeting at two different times, which is worse than a refusal.
        with transaction.atomic():
            for field, value in validated_data.items():
                setattr(instance, field, value)
            instance.save()
            try:
                instance.retitle_occurrences(was_titled, was_described)
                if start_shift or end_shift:
                    instance.shift_occurrences(start_shift, end_shift)
                instance.extend()
                instance.cancel_beyond(by=self.context["request"].user)
            except DjangoValidationError as error:
                raise serializers.ValidationError(
                    {
                        "start_shift_minutes": [
                            "En af gangene støder sammen med en anden booking: "
                            f"{'; '.join(sum(error.message_dict.values(), []))}"
                        ]
                    }
                ) from error
        return instance


class BookingSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingSettings
        fields = [
            "private_bookings_enabled",
            "private_booking_min_notice_days",
            "private_booking_max_horizon_days",
            "private_booking_weekdays",
            "public_bookings_open",
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
