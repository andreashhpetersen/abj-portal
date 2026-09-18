"""
Booking of the community room (beboerlokale).

There is one room, so an `Event` needs no room reference and two events simply
may not overlap in time. If the association ever gets a second bookable space,
add a `room` FK here and include it in `_clashing_events` — that is the only
place the "one room" assumption lives.

Rules enforced at the model layer, so they hold no matter what creates an event
— the API, the admin, a shell session or an import:

* an event ends after it starts, and no booking starts in the past
* two live events never overlap (a cancelled one frees its slot)
* public events carry a title; private ones carry none, since the calendar
  shows only who booked and how to reach them
* private bookings respect the association's toggle and booking horizon when
  they are made, and again whenever an edit moves them to another day

Admins are exempt from the private-booking restrictions: the brief gives them
the ability to edit and delete everything, which is worth little if they cannot
place a booking on a resident's behalf.
"""

from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

#: Refuse to materialise an unbounded series. Roughly daily for a year.
MAX_OCCURRENCES = 400


class EventCategory(models.TextChoices):
    PRIVATE = "private", _("private")
    PUBLIC = "public", _("public")


class Frequency(models.TextChoices):
    DAILY = "daily", _("daily")
    WEEKLY = "weekly", _("weekly")
    MONTHLY = "monthly", _("monthly")


def all_weekdays():
    """Default for `private_booking_weekdays` — every day is bookable."""
    return list(range(7))


class BookingSettings(models.Model):
    """Association-wide booking policy. A singleton, always row 1.

    Kept in the database rather than in `settings.py` because the brief asks
    admins to be able to turn private booking on and off, and to decide which
    weekdays the room is available for private use.

    A private booking must be made **at least** `min_notice` days ahead — it is
    a notice period, so neighbours know the room is spoken for — and no more
    than `max_horizon` days ahead, so the calendar cannot be blocked a year out.

    `public_bookings_open` gates who may create a public event rather than when
    — unlike the private-booking fields above, so it is checked in
    `EventSerializer.validate()`, not `Event.clean()`. The model only ever sees
    the organizer a booking ends up with, never who submitted the request, and
    the rule needs both: a resident may still be handed the room by the
    beboerlokalegruppe while this is off.
    """

    private_bookings_enabled = models.BooleanField(
        _("private bookings enabled"),
        default=True,
        help_text=_("When off, only admins can create private bookings."),
    )
    private_booking_min_notice_days = models.PositiveSmallIntegerField(
        _("private booking notice (days)"),
        default=14,
        help_text=_("Residents must book at least this many days ahead."),
    )
    private_booking_max_horizon_days = models.PositiveSmallIntegerField(
        _("private booking horizon (days)"),
        default=90,
        help_text=_("And no more than this many days ahead. 90 ≈ three months."),
    )
    private_booking_weekdays = models.JSONField(
        _("private booking weekdays"),
        default=all_weekdays,
        help_text=_("Weekdays a private booking may start on. 0 = Monday, 6 = Sunday."),
    )
    public_bookings_open = models.BooleanField(
        _("public bookings open to everyone"),
        default=False,
        help_text=_(
            "When off, only the beboerlokalegruppe and admins may create a public "
            "booking. Booking one in someone else's name stays restricted to them "
            "even once this is turned on."
        ),
    )

    class Meta:
        verbose_name = _("booking settings")
        verbose_name_plural = _("booking settings")

    def __str__(self):
        return str(_("Booking settings"))

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def clean(self):
        weekdays = self.private_booking_weekdays
        if not isinstance(weekdays, list) or any(
            not isinstance(day, int) or day < 0 or day > 6 for day in weekdays
        ):
            raise ValidationError(
                {"private_booking_weekdays": _("Use a list of whole numbers from 0 to 6.")}
            )
        if not weekdays:
            raise ValidationError(
                {
                    "private_booking_weekdays": _(
                        "Pick at least one weekday. To stop private bookings "
                        "altogether, turn them off instead."
                    )
                }
            )
        if self.private_booking_max_horizon_days < self.private_booking_min_notice_days:
            raise ValidationError(
                {
                    "private_booking_max_horizon_days": _(
                        "The horizon must be at least as far ahead as the notice period."
                    )
                }
            )

    def delete(self, *args, **kwargs):
        """The singleton is never deleted — there must always be a policy."""

    @classmethod
    def load(cls):
        settings_row, _created = cls.objects.get_or_create(pk=1)
        return settings_row


class EventSeries(models.Model):
    """A repeating public event, e.g. a board-game café every second Tuesday.

    Occurrences are materialised as real `Event` rows rather than expanded on
    the fly. That keeps the calendar a plain date-range query, and lets a single
    occurrence be cancelled or moved without special-casing the pattern.
    """

    title = models.CharField(_("title"), max_length=200)
    description = models.TextField(_("description"), blank=True)
    frequency = models.CharField(_("frequency"), max_length=16, choices=Frequency.choices)
    interval = models.PositiveSmallIntegerField(
        _("interval"),
        default=1,
        help_text=_("Repeat every N periods — 2 with 'weekly' means every second week."),
    )
    until = models.DateField(_("until"), help_text=_("Last date an occurrence may fall on."))
    # The pattern's origin, not a copy of the first occurrence. An occurrence
    # can be moved by hand, and the pattern must not drift when one is — without
    # this, extending a series would re-materialise it from wherever somebody
    # happened to drag the first evening. Null only on series created before the
    # column existed; `pattern_seed` falls back for those.
    seed_start = models.DateTimeField(_("pattern start"), null=True, blank=True)
    seed_end = models.DateTimeField(_("pattern end"), null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="event_series",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("event series")
        verbose_name_plural = _("event series")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.get_frequency_display()})"

    def clean(self):
        if self.interval < 1:
            raise ValidationError({"interval": _("Interval must be at least 1.")})

    def occurrence_times(self, start, end):
        """Yield (start, end) for every occurrence, first one included.

        The arithmetic is done on local wall-clock time and re-localised, so a
        weekly 19:00 event stays at 19:00 after the clocks change rather than
        drifting to 18:00 or 20:00.
        """
        if end <= start:
            raise ValueError("end must be after start")

        duration = end - start
        current_tz = timezone.get_current_timezone()
        naive_start = timezone.localtime(start, current_tz).replace(tzinfo=None)

        for step in range(MAX_OCCURRENCES):
            occurrence_start = timezone.make_aware(naive_start + self._offset(step), current_tz)
            if timezone.localtime(occurrence_start, current_tz).date() > self.until:
                return
            yield occurrence_start, occurrence_start + duration

    def _offset(self, step):
        periods = self.interval * step
        if self.frequency == Frequency.DAILY:
            return relativedelta(days=periods)
        if self.frequency == Frequency.WEEKLY:
            return relativedelta(weeks=periods)
        return relativedelta(months=periods)

    def create_occurrences(self, start, end, after=None):
        """Materialise the series. Returns the created events in date order.

        `after` skips everything up to and including that instant, which is how
        a later `until` adds evenings to the end of a series without disturbing
        the ones already on the calendar.
        """
        created = []
        for occurrence_start, occurrence_end in self.occurrence_times(start, end):
            if after is not None and occurrence_start <= after:
                continue
            created.append(
                Event.objects.create(
                    category=EventCategory.PUBLIC,
                    title=self.title,
                    description=self.description,
                    start=occurrence_start,
                    end=occurrence_end,
                    created_by=self.created_by,
                    series=self,
                )
            )
        return created

    def pattern_seed(self):
        """The times the pattern is generated from, or (None, None) if unknown.

        Older series predate `seed_start`, so fall back to the earliest
        occurrence — which is what they were generated from anyway.
        """
        if self.seed_start and self.seed_end:
            return self.seed_start, self.seed_end
        first = self.occurrences.order_by("start").first()
        return (first.start, first.end) if first else (None, None)

    def upcoming_occurrences(self):
        """The occurrences a change to the series may touch.

        Not the past ones: an evening that has happened is a record of what
        happened, and renaming it afterwards makes the archive lie. Not the
        cancelled ones either — somebody settled those by hand.
        """
        return self.occurrences.filter(
            cancelled_at__isnull=True, start__gte=timezone.now()
        ).order_by("start")

    def retitle_occurrences(self, was_titled, was_described):
        """Push the series' words onto its upcoming occurrences.

        An occurrence somebody has already retitled by hand keeps its own: it
        says something the series does not, and a rename of the series is no
        reason to lose it. That is what the previous values are for — they say
        which occurrences were still speaking for the series.
        """
        changed = []
        for occurrence in self.upcoming_occurrences():
            speaks_for_the_series = (
                occurrence.title == was_titled and occurrence.description == was_described
            )
            if not speaks_for_the_series:
                continue
            if occurrence.title == self.title and occurrence.description == self.description:
                continue
            occurrence.title = self.title
            occurrence.description = self.description
            occurrence.save()
            changed.append(occurrence)
        return changed

    def shift_occurrences(self, start_delta, end_delta):
        """Move every upcoming occurrence by the same amount of wall-clock time.

        A delta rather than a recomputed time, so an occurrence somebody moved
        to another evening moves with the rest instead of snapping back, and a
        series that meets for longer on one date keeps that difference. The
        origin moves too — otherwise extending the series later would put the
        new evenings back at the old hour.
        """
        for occurrence in self.upcoming_occurrences():
            occurrence.start = shift_wall_clock(occurrence.start, start_delta)
            occurrence.end = shift_wall_clock(occurrence.end, end_delta)
            occurrence.save()

        seed_start, seed_end = self.pattern_seed()
        if seed_start is not None:
            self.seed_start = shift_wall_clock(seed_start, start_delta)
            self.seed_end = shift_wall_clock(seed_end, end_delta)
            self.save()

    def extend(self):
        """Materialise the evenings a later `until` now reaches.

        Only ever appends. The gap left by a cancelled or moved occurrence is a
        decision somebody made, and refilling it would quietly undo them.
        """
        seed_start, seed_end = self.pattern_seed()
        if seed_start is None:
            return []
        last = self.occurrences.order_by("-start").first()
        return self.create_occurrences(seed_start, seed_end, after=last.start if last else None)

    def cancel_beyond(self, by):
        """Cancel the upcoming occurrences an earlier `until` no longer covers.

        Cancelled, not deleted: people may have signed up for them, and the
        `EventAttendance` rows would go with the events.
        """
        cancelled = []
        for occurrence in self.upcoming_occurrences():
            if timezone.localdate(occurrence.start) > self.until:
                occurrence.cancel(by=by)
                cancelled.append(occurrence)
        return cancelled


def shift_wall_clock(moment, delta):
    """Move an instant by local wall-clock time rather than by elapsed time.

    Same reasoning as `occurrence_times`: an evening moved half an hour later
    should read 19:30 on both sides of a clock change, not 18:30 on one of them.
    """
    current_tz = timezone.get_current_timezone()
    naive = timezone.localtime(moment, current_tz).replace(tzinfo=None) + delta
    return timezone.make_aware(naive, current_tz)


class Event(models.Model):
    """One booking of the room, private or public."""

    category = models.CharField(_("category"), max_length=16, choices=EventCategory.choices)
    title = models.CharField(_("title"), max_length=200, blank=True)
    description = models.TextField(_("description"), blank=True)
    start = models.DateTimeField(_("start"))
    end = models.DateTimeField(_("end"))
    # PROTECT, not CASCADE: deleting a user must not silently erase the record
    # of who booked the room. Deactivate departed residents instead.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="events",
        verbose_name=_("created by"),
    )
    series = models.ForeignKey(
        EventSeries,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="occurrences",
        verbose_name=_("series"),
    )
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cancelled_events",
        verbose_name=_("cancelled by"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("event")
        verbose_name_plural = _("events")
        ordering = ["start"]
        indexes = [models.Index(fields=["start", "end"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end__gt=models.F("start")), name="event_ends_after_it_starts"
            ),
        ]

    def __str__(self):
        when = timezone.localtime(self.start).strftime("%d-%m-%Y %H:%M")
        return f"{self.title or self.get_category_display()} — {when}"

    def save(self, *args, **kwargs):
        # Validate on every write: DRF's ModelSerializer does not call
        # full_clean(), so without this the API could store an overlapping or
        # out-of-horizon booking that the admin would refuse.
        self.full_clean()
        super().save(*args, **kwargs)

    @classmethod
    def from_db(cls, db, field_names, values):
        """Remember where the booking stood, so `clean` can tell an edit that
        re-places it from one that merely adjusts it."""
        instance = super().from_db(db, field_names, values)
        instance._placed_as = (instance.category, instance.start)
        return instance

    @property
    def is_cancelled(self):
        return self.cancelled_at is not None

    @property
    def is_public(self):
        return self.category == EventCategory.PUBLIC

    @property
    def contact_email(self):
        """Who to contact about this booking — shown on the calendar."""
        return self.created_by.email

    @property
    def contact_phone(self):
        return self.created_by.phone

    def cancel(self, by):
        """Soft-cancel. The slot frees up, but the record survives."""
        if self.is_cancelled:
            return
        self.cancelled_at = timezone.now()
        self.cancelled_by = by
        self.save()

    def _clashing_events(self):
        """Live events overlapping this one. Touching at the edges is fine."""
        clashes = Event.objects.filter(
            cancelled_at__isnull=True,
            start__lt=self.end,
            end__gt=self.start,
        )
        if self.pk:
            clashes = clashes.exclude(pk=self.pk)
        return clashes

    def clean(self):
        errors = {}

        if self.start and self.end and self.end <= self.start:
            errors["end"] = _("The event must end after it starts.")

        if self.start and self.start < timezone.now() and (self._state.adding or self._start_moved):
            errors["start"] = _("The event cannot start in the past.")

        if self.category == EventCategory.PUBLIC and not self.title:
            errors["title"] = _("Public events need a title.")

        if self.category == EventCategory.PRIVATE:
            # Private bookings show only who booked and how to reach them, so
            # there is nothing here to leak on a shared calendar.
            if self.title:
                errors["title"] = _("Private bookings have no title.")
            if self.description:
                errors["description"] = _("Private bookings have no description.")

        if not errors and self.start and self.end and self._clashing_events().exists():
            errors["start"] = _("The room is already booked in that period.")

        # Only when the booking is placed. The toggle and horizon govern whether
        # a booking may be *made*; testing them on every save would strand
        # existing bookings in a state where they cannot even be cancelled.
        if self._is_being_placed and self.category == EventCategory.PRIVATE and self.start:
            errors.update(self._private_booking_errors())

        if errors:
            raise ValidationError(errors)

    @property
    def _start_moved(self):
        """Whether this save gives the booking a start it did not have."""
        placed_as = getattr(self, "_placed_as", None)
        return placed_as is not None and placed_as[1] != self.start

    @property
    def _is_being_placed(self):
        """Whether this save claims a slot the booking did not already hold.

        Creating it, of course, but also moving it to another day or turning a
        public event private — each is a resident claiming the room under the
        private-booking policy, so each has to satisfy it. Editing an existing
        booking is otherwise not a fresh claim: shifting the hour within the
        booked day, correcting the finishing time, or cancelling all leave the
        day the neighbours were told about unchanged, and re-testing the notice
        period on those would make a booking uneditable the moment it came
        within a fortnight — and uncancellable once private bookings closed.
        """
        if self._state.adding:
            return True
        placed_as = getattr(self, "_placed_as", None)
        if placed_as is None or self.start is None:
            return False
        category, start = placed_as
        return self.category != category or (
            timezone.localdate(start) != timezone.localdate(self.start)
        )

    def _private_booking_errors(self):
        """The policy for private bookings, none of which binds an admin."""
        booked_by_admin = self.created_by_id and self.created_by.is_staff
        if booked_by_admin:
            return {}

        policy = BookingSettings.load()
        if not policy.private_bookings_enabled:
            return {"category": _("Private bookings are currently closed.")}

        problems = []

        # Whole calendar days, not a moving timestamp: booking something 14 days
        # out at 10:00 should not be refused merely because it is 15:00 today.
        today = timezone.localdate()
        booked_for = timezone.localtime(self.start).date()
        earliest = today + timedelta(days=policy.private_booking_min_notice_days)
        latest = today + timedelta(days=policy.private_booking_max_horizon_days)
        if booked_for < earliest:
            problems.append(
                _("Private bookings must be made at least %(days)d days ahead.")
                % {"days": policy.private_booking_min_notice_days}
            )
        elif booked_for > latest:
            problems.append(
                _("Private bookings can be made at most %(days)d days ahead.")
                % {"days": policy.private_booking_max_horizon_days}
            )

        # Judged on the day the booking starts, so an evening running past
        # midnight is allowed by the weekday it began on.
        weekday = timezone.localtime(self.start).weekday()
        if weekday not in policy.private_booking_weekdays:
            problems.append(_("The room is not available for private bookings that weekday."))

        return {"start": problems} if problems else {}


class EventAttendance(models.Model):
    """A resident saying they will turn up to a public event."""

    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="attendances",
        verbose_name=_("event"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attendances",
        verbose_name=_("user"),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("attendance")
        verbose_name_plural = _("attendances")
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["event", "user"], name="one_attendance_per_person"),
        ]

    def __str__(self):
        return f"{self.user} → {self.event}"

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        if self.event_id and not self.event.is_public:
            raise ValidationError({"event": _("Only public events take attendance.")})
        if self.event_id and self.event.is_cancelled:
            raise ValidationError({"event": _("The event is cancelled.")})
