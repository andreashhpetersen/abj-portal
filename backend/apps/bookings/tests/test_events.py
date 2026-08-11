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


def book(user, days_ahead=3, category=EventCategory.PRIVATE, hours=2, **extra):
    start, end = slot(days_ahead, hours)
    return Event.objects.create(category=category, start=start, end=end, created_by=user, **extra)


def test_an_event_must_end_after_it_starts(resident):
    start, _end = slot(3)
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
    existing = book(resident, days_ahead=5, hours=4)
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
    first = book(resident, days_ahead=5, hours=2)
    back_to_back = Event(
        category=EventCategory.PRIVATE,
        start=first.end,
        end=first.end + timedelta(hours=2),
        created_by=other_resident,
    )
    back_to_back.full_clean()  # does not raise


def test_a_cancelled_booking_frees_the_slot(resident, other_resident):
    first = book(resident, days_ahead=6)
    first.cancel(by=resident)

    replacement = Event(
        category=EventCategory.PRIVATE,
        start=first.start,
        end=first.end,
        created_by=other_resident,
    )
    replacement.full_clean()  # does not raise


def test_editing_an_event_does_not_clash_with_itself(resident):
    event = book(resident, days_ahead=7)
    event.end = event.end + timedelta(hours=1)
    event.save()  # does not raise
    assert Event.objects.get(pk=event.pk).end == event.end


def test_public_events_need_a_title(resident):
    start, end = slot(4)
    event = Event(category=EventCategory.PUBLIC, start=start, end=end, created_by=resident)
    with pytest.raises(ValidationError) as caught:
        event.full_clean()
    assert "title" in caught.value.error_dict


def test_private_bookings_carry_no_title(resident):
    start, end = slot(4)
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


def test_a_resident_cannot_book_privately_beyond_the_horizon(resident):
    start, end = slot(30)
    event = Event(category=EventCategory.PRIVATE, start=start, end=end, created_by=resident)
    with pytest.raises(ValidationError) as caught:
        event.full_clean()
    assert "start" in caught.value.error_dict


def test_a_resident_can_book_privately_inside_the_horizon(resident):
    book(resident, days_ahead=13)  # does not raise
    assert Event.objects.count() == 1


def test_an_admin_may_book_privately_beyond_the_horizon(admin_user):
    book(admin_user, days_ahead=90)  # does not raise
    assert Event.objects.count() == 1


def test_public_events_ignore_the_private_horizon(resident):
    book(resident, days_ahead=90, category=EventCategory.PUBLIC, title="Sommerfest")
    assert Event.objects.count() == 1


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

    book(admin_user, days_ahead=3)  # does not raise
    assert Event.objects.count() == 1


def test_closing_private_bookings_does_not_strand_existing_ones(resident):
    """Regression: the toggle governs new bookings, not existing ones."""
    event = book(resident, days_ahead=3)

    policy = BookingSettings.load()
    policy.private_bookings_enabled = False
    policy.save()

    event.cancel(by=resident)  # must not raise
    assert Event.objects.get(pk=event.pk).is_cancelled is True


def test_cancelling_records_who_did_it(resident, admin_user):
    event = book(resident, days_ahead=3)
    event.cancel(by=admin_user)

    event.refresh_from_db()
    assert event.is_cancelled is True
    assert event.cancelled_by == admin_user
    assert event.cancelled_at is not None


def test_cancelling_twice_keeps_the_first_cancellation(resident, admin_user):
    event = book(resident, days_ahead=3)
    event.cancel(by=resident)
    first_time = event.cancelled_at

    event.cancel(by=admin_user)
    assert event.cancelled_at == first_time
    assert event.cancelled_by == resident


def test_contact_details_come_from_whoever_booked(resident):
    resident.phone = "+45 12 34 56 78"
    resident.save()
    event = book(resident, days_ahead=3)

    assert event.contact_email == "beboer@example.dk"
    assert event.contact_phone == "+45 12 34 56 78"


def test_booking_settings_are_a_singleton(db):
    first = BookingSettings.load()
    first.private_booking_horizon_days = 21
    first.save()

    assert BookingSettings.load().pk == first.pk
    assert BookingSettings.objects.count() == 1
    assert BookingSettings.load().private_booking_horizon_days == 21


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
    event = book(resident, days_ahead=3)
    with pytest.raises(ValidationError) as caught:
        EventAttendance.objects.create(event=event, user=other_resident)
    assert "event" in caught.value.error_dict


def test_a_cancelled_event_takes_no_attendance(resident, other_resident):
    event = book(resident, days_ahead=20, category=EventCategory.PUBLIC, title="Fastelavn")
    event.cancel(by=resident)

    with pytest.raises(ValidationError):
        EventAttendance.objects.create(event=event, user=other_resident)
