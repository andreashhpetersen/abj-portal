"""The booking API: who may do what, and how the model's rules reach the client.

Model rules are tested in test_events.py. These tests check that the API applies
them, reports them as field-level 400s rather than 500s, and enforces the right
permissions.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bookings.models import BookingSettings, Event, EventCategory, EventSeries, Frequency

User = get_user_model()


@pytest.fixture
def resident(db):
    return User.objects.create_user(email="beboer@example.dk", password="hemmeligt123")


@pytest.fixture
def neighbour(db):
    return User.objects.create_user(email="nabo@example.dk", password="hemmeligt123")


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        email="formand@example.dk", password="hemmeligt123", is_staff=True
    )


@pytest.fixture
def api():
    return APIClient()


def as_user(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def booking_payload(days_ahead=3, hours=2, **overrides):
    start = timezone.now() + timedelta(days=days_ahead)
    payload = {
        "category": EventCategory.PRIVATE,
        "start": start.isoformat(),
        "end": (start + timedelta(hours=hours)).isoformat(),
    }
    payload.update(overrides)
    return payload


def make_event(user, days_ahead=3, **overrides):
    start = timezone.now() + timedelta(days=days_ahead)
    fields = {
        "category": EventCategory.PRIVATE,
        "start": start,
        "end": start + timedelta(hours=2),
        "created_by": user,
    }
    fields.update(overrides)
    return Event.objects.create(**fields)


# --- access -----------------------------------------------------------------


def test_the_calendar_requires_a_login(api, db):
    assert api.get(reverse("bookings:event-list")).status_code == 403


def test_any_logged_in_user_sees_the_calendar(resident, neighbour):
    make_event(neighbour, days_ahead=4)
    response = as_user(resident).get(reverse("bookings:event-list"))

    assert response.status_code == 200
    assert len(response.json()) == 1


# --- creating ---------------------------------------------------------------


def test_a_resident_can_book_the_room(resident):
    response = as_user(resident).post(
        reverse("bookings:event-list"), booking_payload(), format="json"
    )

    assert response.status_code == 201
    assert response.json()["created_by"]["email"] == "beboer@example.dk"
    assert Event.objects.count() == 1


def test_you_always_book_in_your_own_name(resident, neighbour):
    """A client-supplied owner must be ignored, not honoured."""
    response = as_user(resident).post(
        reverse("bookings:event-list"),
        booking_payload(created_by=neighbour.pk),
        format="json",
    )

    assert response.status_code == 201
    assert Event.objects.get().created_by == resident


def test_a_clashing_booking_is_rejected_with_a_field_error(resident, neighbour):
    existing = make_event(neighbour, days_ahead=5)
    response = as_user(resident).post(
        reverse("bookings:event-list"),
        booking_payload(
            start=(existing.start + timedelta(minutes=30)).isoformat(),
            end=(existing.end + timedelta(minutes=30)).isoformat(),
        ),
        format="json",
    )

    assert response.status_code == 400
    assert "start" in response.json()


def test_booking_beyond_the_horizon_is_rejected(resident):
    response = as_user(resident).post(
        reverse("bookings:event-list"), booking_payload(days_ahead=40), format="json"
    )

    assert response.status_code == 400
    assert "start" in response.json()


def test_an_admin_may_book_beyond_the_horizon(admin_user):
    response = as_user(admin_user).post(
        reverse("bookings:event-list"), booking_payload(days_ahead=40), format="json"
    )
    assert response.status_code == 201


def test_a_public_event_without_a_title_is_rejected(resident):
    response = as_user(resident).post(
        reverse("bookings:event-list"),
        booking_payload(category=EventCategory.PUBLIC),
        format="json",
    )

    assert response.status_code == 400
    assert "title" in response.json()


# --- editing, cancelling, deleting ------------------------------------------


def test_the_owner_can_cancel_their_booking(resident):
    event = make_event(resident)
    response = as_user(resident).post(reverse("bookings:event-cancel", args=[event.pk]))

    assert response.status_code == 200
    assert response.json()["is_cancelled"] is True
    event.refresh_from_db()
    assert event.cancelled_by == resident


def test_someone_else_cannot_cancel_your_booking(resident, neighbour):
    event = make_event(resident)
    response = as_user(neighbour).post(reverse("bookings:event-cancel", args=[event.pk]))

    assert response.status_code == 403
    event.refresh_from_db()
    assert event.is_cancelled is False


def test_an_admin_can_cancel_anyones_booking(resident, admin_user):
    event = make_event(resident)
    response = as_user(admin_user).post(reverse("bookings:event-cancel", args=[event.pk]))

    assert response.status_code == 200
    event.refresh_from_db()
    assert event.cancelled_by == admin_user


def test_cancelling_twice_is_a_bad_request(resident):
    event = make_event(resident)
    client = as_user(resident)
    client.post(reverse("bookings:event-cancel", args=[event.pk]))

    assert client.post(reverse("bookings:event-cancel", args=[event.pk])).status_code == 400


def test_a_cancelled_booking_is_still_reachable_on_its_own_url(resident):
    """Regression: hiding cancelled bookings from the calendar must not hide
    them from the detail route, or they become impossible to inspect."""
    event = make_event(resident)
    event.cancel(by=resident)

    response = as_user(resident).get(reverse("bookings:event-detail", args=[event.pk]))
    assert response.status_code == 200
    assert response.json()["is_cancelled"] is True


def test_an_admin_can_delete_a_cancelled_booking(resident, admin_user):
    event = make_event(resident)
    event.cancel(by=resident)

    response = as_user(admin_user).delete(reverse("bookings:event-detail", args=[event.pk]))
    assert response.status_code == 204
    assert Event.objects.count() == 0


def test_the_owner_can_move_their_booking(resident):
    event = make_event(resident, days_ahead=6)
    new_start = event.start + timedelta(hours=1)
    response = as_user(resident).patch(
        reverse("bookings:event-detail", args=[event.pk]),
        {"start": new_start.isoformat(), "end": (new_start + timedelta(hours=2)).isoformat()},
        format="json",
    )

    assert response.status_code == 200
    event.refresh_from_db()
    assert event.start == new_start


def test_someone_else_cannot_move_your_booking(resident, neighbour):
    event = make_event(resident, days_ahead=6)
    response = as_user(neighbour).patch(
        reverse("bookings:event-detail", args=[event.pk]),
        {"title": "Kapret"},
        format="json",
    )
    assert response.status_code == 403


def test_only_an_admin_may_delete_a_booking_outright(resident, admin_user):
    event = make_event(resident)

    assert (
        as_user(resident).delete(reverse("bookings:event-detail", args=[event.pk])).status_code
        == 403
    )
    assert (
        as_user(admin_user).delete(reverse("bookings:event-detail", args=[event.pk])).status_code
        == 204
    )
    assert Event.objects.count() == 0


# --- the calendar window ----------------------------------------------------


def test_the_window_includes_events_that_merely_overlap_it(resident):
    event = make_event(resident, days_ahead=5)
    day = timezone.localtime(event.start).date()

    response = as_user(resident).get(
        reverse("bookings:event-list"), {"from": day.isoformat(), "to": day.isoformat()}
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [event.pk]


def test_events_outside_the_window_are_left_out(resident):
    event = make_event(resident, days_ahead=5)
    far_off = (timezone.localtime(event.start) + timedelta(days=3)).date()

    response = as_user(resident).get(
        reverse("bookings:event-list"), {"from": far_off.isoformat(), "to": far_off.isoformat()}
    )
    assert response.json() == []


def test_a_malformed_date_is_a_bad_request(resident):
    response = as_user(resident).get(reverse("bookings:event-list"), {"from": "5. maj"})
    assert response.status_code == 400


def test_cancelled_bookings_are_hidden_unless_asked_for(resident):
    event = make_event(resident)
    event.cancel(by=resident)
    client = as_user(resident)

    assert client.get(reverse("bookings:event-list")).json() == []
    with_cancelled = client.get(reverse("bookings:event-list"), {"include_cancelled": "true"})
    assert len(with_cancelled.json()) == 1


def test_the_calendar_shows_contact_details_for_whoever_booked(resident, neighbour):
    neighbour.phone = "+45 12 34 56 78"
    neighbour.save()
    make_event(neighbour, days_ahead=4)

    booking = as_user(resident).get(reverse("bookings:event-list")).json()[0]
    assert booking["created_by"]["email"] == "nabo@example.dk"
    assert booking["created_by"]["phone"] == "+45 12 34 56 78"
    assert booking["can_cancel"] is False


# --- attendance -------------------------------------------------------------


def test_a_resident_can_sign_up_and_withdraw(resident, neighbour):
    event = make_event(neighbour, days_ahead=20, category=EventCategory.PUBLIC, title="Fastelavn")
    client = as_user(resident)
    url = reverse("bookings:event-attendance", args=[event.pk])

    signed_up = client.post(url)
    assert signed_up.status_code == 201
    assert signed_up.json()["attendee_count"] == 1
    assert signed_up.json()["is_attending"] is True

    assert client.delete(url).status_code == 204
    assert event.attendances.count() == 0


def test_signing_up_twice_is_a_bad_request(resident, neighbour):
    event = make_event(neighbour, days_ahead=20, category=EventCategory.PUBLIC, title="Fastelavn")
    client = as_user(resident)
    url = reverse("bookings:event-attendance", args=[event.pk])
    client.post(url)

    assert client.post(url).status_code == 400


def test_private_bookings_take_no_attendance(resident, neighbour):
    event = make_event(neighbour, days_ahead=3)
    response = as_user(resident).post(reverse("bookings:event-attendance", args=[event.pk]))

    assert response.status_code == 400


# --- recurring events -------------------------------------------------------


def test_creating_a_series_returns_its_occurrences(resident):
    start = timezone.now() + timedelta(days=2)
    response = as_user(resident).post(
        reverse("bookings:series-list"),
        {
            "title": "Brætspilscafé",
            "description": "Kom og spil.",
            "frequency": Frequency.WEEKLY,
            "interval": 1,
            "until": (timezone.localtime(start) + timedelta(days=14)).date().isoformat(),
            "start": start.isoformat(),
            "end": (start + timedelta(hours=3)).isoformat(),
        },
        format="json",
    )

    assert response.status_code == 201
    assert len(response.json()["occurrences"]) == 3
    assert Event.objects.count() == 3


def test_a_clashing_series_leaves_nothing_behind(resident, neighbour):
    """The whole series is rolled back — half a series is worse than none."""
    start = timezone.now() + timedelta(days=2)
    make_event(neighbour, days_ahead=9)  # sits on the second occurrence's week

    clash = Event.objects.get()
    response = as_user(resident).post(
        reverse("bookings:series-list"),
        {
            "title": "Brætspilscafé",
            "frequency": Frequency.WEEKLY,
            "interval": 1,
            "until": (timezone.localtime(start) + timedelta(days=14)).date().isoformat(),
            "start": clash.start.isoformat(),
            "end": (clash.start + timedelta(hours=3)).isoformat(),
        },
        format="json",
    )

    assert response.status_code == 400
    assert EventSeries.objects.count() == 0
    assert Event.objects.count() == 1  # only the pre-existing booking survives


# --- the booking policy -----------------------------------------------------


def test_anyone_logged_in_can_read_the_policy(resident):
    response = as_user(resident).get(reverse("bookings:settings"))

    assert response.status_code == 200
    assert response.json()["private_booking_horizon_days"] == 14


def test_only_admins_may_change_the_policy(resident, admin_user):
    url = reverse("bookings:settings")

    assert (
        as_user(resident).patch(url, {"private_bookings_enabled": False}, format="json").status_code
        == 403
    )

    response = as_user(admin_user).patch(url, {"private_bookings_enabled": False}, format="json")
    assert response.status_code == 200
    assert BookingSettings.load().private_bookings_enabled is False


def test_closing_private_bookings_blocks_residents_through_the_api(resident):
    policy = BookingSettings.load()
    policy.private_bookings_enabled = False
    policy.save()

    response = as_user(resident).post(
        reverse("bookings:event-list"), booking_payload(), format="json"
    )

    assert response.status_code == 400
    assert "category" in response.json()
