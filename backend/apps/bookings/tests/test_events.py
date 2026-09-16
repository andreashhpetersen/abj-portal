"""The booking rules: overlaps, the private-booking policy, and cancellation."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.bookings.models import BookingSettings, Event, EventAttendance, EventCategory

User = get_user_model()


def slot(days_ahead, hours=2):
    """A start/end pair that far ahead. Different offsets never overlap."""
    start = timezone.now() + timedelta(days=days_ahead)
    return start, start + timedelta(hours=hours)


@pytest.fixture
def resident(db):
    return User.objects.create_user(email="beboer@example.dk", password="hemmeligt123")


@pytest.fixture
def other_resident(db):
    return User.objects.create_user(email="nabo@example.dk", password="hemmeligt123")


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        email="formand@example.dk", password="hemmeligt123", is_staff=True
    )


def book(user, days_ahead=20, category=EventCategory.PRIVATE, hours=2, **extra):
    start, end = slot(days_ahead, hours)
    return Event.objects.create(category=category, start=start, end=end, created_by=user, **extra)


def test_an_event_must_end_after_it_starts(resident):
    start, _end = slot(20)
    event = Event(category=EventCategory.PRIVATE, start=start, end=start, created_by=resident)
    with pytest.raises(ValidationError) as caught:
        event.full_clean()
    assert "end" in caught.value.error_dict


def test_an_event_cannot_be_created_in_the_past(resident):
    start = timezone.now() - timedelta(hours=3)
    event = Event(
        category=EventCategory.PRIVATE,
        start=start,
        end=start + timedelta(hours=2),
        created_by=resident,
    )
    with pytest.raises(ValidationError) as caught:
        event.full_clean()
    assert "start" in caught.value.error_dict


def test_the_room_cannot_be_double_booked(resident, other_resident):
    existing = book(resident, days_ahead=25, hours=4)
    overlapping = Event(
        category=EventCategory.PRIVATE,
        start=existing.start + timedelta(hours=1),
        end=existing.end + timedelta(hours=1),
        created_by=other_resident,
    )
    with pytest.raises(ValidationError) as caught:
        overlapping.full_clean()
    assert "start" in caught.value.error_dict


def test_bookings_may_touch_at_the_edges(resident, other_resident):
    first = book(resident, days_ahead=25, hours=2)
    back_to_back = Event(
        category=EventCategory.PRIVATE,
        start=first.end,
        end=first.end + timedelta(hours=2),
        created_by=other_resident,
    )
    back_to_back.full_clean()  # does not raise


def test_a_cancelled_booking_frees_the_slot(resident, other_resident):
    first = book(resident, days_ahead=26)
    first.cancel(by=resident)

    replacement = Event(
        category=EventCategory.PRIVATE,
        start=first.start,
        end=first.end,
        created_by=other_resident,
    )
    replacement.full_clean()  # does not raise


def test_editing_an_event_does_not_clash_with_itself(resident):
    event = book(resident, days_ahead=27)
    event.end = event.end + timedelta(hours=1)
    event.save()  # does not raise
    assert Event.objects.get(pk=event.pk).end == event.end


def test_public_events_need_a_title(resident):
    start, end = slot(20)
    event = Event(category=EventCategory.PUBLIC, start=start, end=end, created_by=resident)
    with pytest.raises(ValidationError) as caught:
        event.full_clean()
    assert "title" in caught.value.error_dict


def test_private_bookings_carry_no_title(resident):
    start, end = slot(20)
    event = Event(
        category=EventCategory.PRIVATE,
        title="Fødselsdag",
        start=start,
        end=end,
        created_by=resident,
    )
    with pytest.raises(ValidationError) as caught:
        event.full_clean()
    assert "title" in caught.value.error_dict


def test_a_private_booking_needs_two_weeks_notice(resident):
    """The 14 days is a notice period, not a ceiling."""
    start, end = slot(5)
    event = Event(category=EventCategory.PRIVATE, start=start, end=end, created_by=resident)
    with pytest.raises(ValidationError) as caught:
        event.full_clean()
    assert "start" in caught.value.error_dict


def test_a_private_booking_exactly_at_the_notice_period_is_accepted(resident):
    """The boundary counts whole days, so booking day 14 works at any hour."""
    book(resident, days_ahead=14)  # does not raise
    assert Event.objects.count() == 1


def test_a_private_booking_just_inside_the_notice_period_is_accepted(resident):
    book(resident, days_ahead=15)  # does not raise
    assert Event.objects.count() == 1


def test_a_private_booking_exactly_at_the_horizon_is_accepted(resident):
    book(resident, days_ahead=90)  # does not raise
    assert Event.objects.count() == 1


def test_a_private_booking_cannot_be_more_than_three_months_out(resident):
    start, end = slot(120)
    event = Event(category=EventCategory.PRIVATE, start=start, end=end, created_by=resident)
    with pytest.raises(ValidationError) as caught:
        event.full_clean()
    assert "start" in caught.value.error_dict


def test_a_private_booking_inside_three_months_is_accepted(resident):
    book(resident, days_ahead=80)  # does not raise
    assert Event.objects.count() == 1


def test_an_admin_is_bound_by_neither_end_of_the_window(admin_user):
    book(admin_user, days_ahead=1)
    book(admin_user, days_ahead=200)
    assert Event.objects.count() == 2


def test_public_events_ignore_the_private_booking_window(resident):
    book(resident, days_ahead=2, category=EventCategory.PUBLIC, title="Sommerfest")
    book(resident, days_ahead=200, category=EventCategory.PUBLIC, title="Nytår")
    assert Event.objects.count() == 2


# --- moving an existing booking ---------------------------------------------


def test_a_booking_cannot_be_moved_into_the_past(resident):
    event = Event.objects.get(pk=book(resident, days_ahead=20).pk)
    event.start = timezone.now() - timedelta(hours=1)
    event.end = event.start + timedelta(hours=2)

    with pytest.raises(ValidationError) as caught:
        event.full_clean()
    assert "start" in caught.value.error_dict


def test_a_private_booking_cannot_be_moved_inside_the_notice_period(resident):
    """Regression: editing must not be a way round the fortnight's notice."""
    event = Event.objects.get(pk=book(resident, days_ahead=20).pk)
    event.start, event.end = slot(3)

    with pytest.raises(ValidationError) as caught:
        event.full_clean()
    assert "start" in caught.value.error_dict


def test_an_existing_private_booking_can_be_retimed_on_its_own_day(resident):
    """The notice period is about which day the room is spoken for. Re-testing
    it on every save would make a booking uneditable exactly as it drew near,
    which is when the times tend to need correcting."""
    booking = book(resident, days_ahead=20)
    tighten_notice_to(30)  # as if the booked day had drawn nearer

    event = Event.objects.get(pk=booking.pk)
    event.start += timedelta(minutes=30)
    event.end += timedelta(hours=2)
    event.full_clean()  # does not raise


def test_an_existing_private_booking_cannot_be_moved_to_a_day_it_could_not_claim(resident):
    booking = book(resident, days_ahead=20)
    tighten_notice_to(30)

    event = Event.objects.get(pk=booking.pk)
    event.start, event.end = slot(25)

    with pytest.raises(ValidationError) as caught:
        event.full_clean()
    assert "start" in caught.value.error_dict


def test_turning_a_public_event_private_applies_the_private_rules(resident):
    """A public event may sit three days out; the same slot claimed privately
    may not, so the change of category is itself a fresh claim."""
    booking = book(resident, days_ahead=3, category=EventCategory.PUBLIC, title="Sommerfest")

    event = Event.objects.get(pk=booking.pk)
    event.category = EventCategory.PRIVATE
    event.title = ""

    with pytest.raises(ValidationError) as caught:
        event.full_clean()
    assert "start" in caught.value.error_dict


def test_a_booking_loaded_from_the_database_can_still_be_cancelled(resident):
    """Regression: the rules a move has to pass must not reach cancellation,
    which leaves the day and the category exactly as they were."""
    booking = book(resident, days_ahead=20)
    policy = BookingSettings.load()
    policy.private_bookings_enabled = False
    policy.save()

    Event.objects.get(pk=booking.pk).cancel(by=resident)  # must not raise
    assert Event.objects.get(pk=booking.pk).is_cancelled is True


def tighten_notice_to(days):
    policy = BookingSettings.load()
    policy.private_booking_min_notice_days = days
    policy.save()


# --- which weekdays the room is available -----------------------------------


def only_weekday(weekday):
    """Restrict private bookings to a single weekday. 0 = Monday."""
    policy = BookingSettings.load()
    policy.private_booking_weekdays = [weekday]
    policy.save()


def a_private_booking_on(resident, weekday, weeks_ahead=4):
    """Build an unsaved private booking landing on that weekday, well ahead."""
    start = timezone.localtime(timezone.now()) + timedelta(weeks=weeks_ahead)
    start += timedelta(days=(weekday - start.weekday()) % 7)
    start = start.replace(hour=18, minute=0, second=0, microsecond=0)
    return Event(
        category=EventCategory.PRIVATE,
        start=start,
        end=start + timedelta(hours=3),
        created_by=resident,
    )


def test_a_private_booking_on_an_allowed_weekday_is_accepted(resident):
    only_weekday(5)  # Saturdays only
    a_private_booking_on(resident, weekday=5).full_clean()  # does not raise


def test_a_private_booking_on_a_closed_weekday_is_rejected(resident):
    only_weekday(5)  # Saturdays only
    with pytest.raises(ValidationError) as caught:
        a_private_booking_on(resident, weekday=2).full_clean()  # a Wednesday
    assert "start" in caught.value.error_dict


def test_the_weekday_rule_does_not_apply_to_public_events(resident):
    only_weekday(5)
    event = a_private_booking_on(resident, weekday=2)
    event.category = EventCategory.PUBLIC
    event.title = "Beboermøde"
    event.full_clean()  # does not raise


def test_an_admin_may_book_on_a_closed_weekday(admin_user):
    only_weekday(5)
    a_private_booking_on(admin_user, weekday=2).full_clean()  # does not raise


def test_the_weekday_is_judged_on_the_day_the_booking_starts(resident):
    """A Saturday party running to 02:00 is a Saturday booking."""
    only_weekday(5)
    event = a_private_booking_on(resident, weekday=5)
    event.start = event.start.replace(hour=20)
    event.end = event.start + timedelta(hours=6)  # into Sunday
    event.full_clean()  # does not raise


def test_the_policy_needs_at_least_one_weekday(db):
    policy = BookingSettings.load()
    policy.private_booking_weekdays = []
    with pytest.raises(ValidationError) as caught:
        policy.full_clean()
    assert "private_booking_weekdays" in caught.value.error_dict


def test_the_horizon_cannot_be_shorter_than_the_notice_period(db):
    policy = BookingSettings.load()
    policy.private_booking_min_notice_days = 30
    policy.private_booking_max_horizon_days = 10
    with pytest.raises(ValidationError) as caught:
        policy.full_clean()
    assert "private_booking_max_horizon_days" in caught.value.error_dict


def test_residents_cannot_book_privately_when_the_toggle_is_off(resident):
    policy = BookingSettings.load()
    policy.private_bookings_enabled = False
    policy.save()

    start, end = slot(3)
    event = Event(category=EventCategory.PRIVATE, start=start, end=end, created_by=resident)
    with pytest.raises(ValidationError) as caught:
        event.full_clean()
    assert "category" in caught.value.error_dict


def test_admins_can_still_book_privately_when_the_toggle_is_off(admin_user):
    policy = BookingSettings.load()
    policy.private_bookings_enabled = False
    policy.save()

    book(admin_user, days_ahead=20)  # does not raise
    assert Event.objects.count() == 1


def test_closing_private_bookings_does_not_strand_existing_ones(resident):
    """Regression: the toggle governs new bookings, not existing ones."""
    event = book(resident, days_ahead=20)

    policy = BookingSettings.load()
    policy.private_bookings_enabled = False
    policy.save()

    event.cancel(by=resident)  # must not raise
    assert Event.objects.get(pk=event.pk).is_cancelled is True


def test_cancelling_records_who_did_it(resident, admin_user):
    event = book(resident, days_ahead=20)
    event.cancel(by=admin_user)

    event.refresh_from_db()
    assert event.is_cancelled is True
    assert event.cancelled_by == admin_user
    assert event.cancelled_at is not None


def test_cancelling_twice_keeps_the_first_cancellation(resident, admin_user):
    event = book(resident, days_ahead=20)
    event.cancel(by=resident)
    first_time = event.cancelled_at

    event.cancel(by=admin_user)
    assert event.cancelled_at == first_time
    assert event.cancelled_by == resident


def test_contact_details_come_from_whoever_booked(resident):
    resident.phone = "+45 12 34 56 78"
    resident.save()
    event = book(resident, days_ahead=20)

    assert event.contact_email == "beboer@example.dk"
    assert event.contact_phone == "+45 12 34 56 78"


def test_booking_settings_are_a_singleton(db):
    first = BookingSettings.load()
    first.private_booking_min_notice_days = 21
    first.save()

    assert BookingSettings.load().pk == first.pk
    assert BookingSettings.objects.count() == 1
    assert BookingSettings.load().private_booking_min_notice_days == 21


def test_a_resident_can_sign_up_for_a_public_event(resident, other_resident):
    event = book(resident, days_ahead=20, category=EventCategory.PUBLIC, title="Fastelavn")
    EventAttendance.objects.create(event=event, user=other_resident)

    assert event.attendances.count() == 1


def test_a_resident_cannot_sign_up_twice(resident, other_resident):
    event = book(resident, days_ahead=20, category=EventCategory.PUBLIC, title="Fastelavn")
    EventAttendance.objects.create(event=event, user=other_resident)

    with pytest.raises(ValidationError):
        EventAttendance.objects.create(event=event, user=other_resident)


def test_private_bookings_do_not_take_attendance(resident, other_resident):
    event = book(resident, days_ahead=20)
    with pytest.raises(ValidationError) as caught:
        EventAttendance.objects.create(event=event, user=other_resident)
    assert "event" in caught.value.error_dict


def test_a_cancelled_event_takes_no_attendance(resident, other_resident):
    event = book(resident, days_ahead=20, category=EventCategory.PUBLIC, title="Fastelavn")
    event.cancel(by=resident)

    with pytest.raises(ValidationError):
        EventAttendance.objects.create(event=event, user=other_resident)
