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


def booking_payload(days_ahead=20, hours=2, **overrides):
    start = timezone.now() + timedelta(days=days_ahead)
    payload = {
        "category": EventCategory.PRIVATE,
        "start": start.isoformat(),
        "end": (start + timedelta(hours=hours)).isoformat(),
    }
    payload.update(overrides)
    return payload


def make_event(user, days_ahead=20, **overrides):
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
    make_event(neighbour, days_ahead=24)
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
    existing = make_event(neighbour, days_ahead=25)
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


def test_booking_with_too_little_notice_is_rejected(resident):
    response = as_user(resident).post(
        reverse("bookings:event-list"), booking_payload(days_ahead=4), format="json"
    )

    assert response.status_code == 400
    assert "start" in response.json()


def test_booking_beyond_three_months_is_rejected(resident):
    response = as_user(resident).post(
        reverse("bookings:event-list"), booking_payload(days_ahead=120), format="json"
    )

    assert response.status_code == 400
    assert "start" in response.json()


def test_an_admin_may_book_at_either_extreme(admin_user):
    client = as_user(admin_user)

    assert (
        client.post(
            reverse("bookings:event-list"), booking_payload(days_ahead=1), format="json"
        ).status_code
        == 201
    )
    assert (
        client.post(
            reverse("bookings:event-list"), booking_payload(days_ahead=200), format="json"
        ).status_code
        == 201
    )


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
    event = make_event(resident, days_ahead=26)
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
    event = make_event(resident, days_ahead=26)
    response = as_user(neighbour).patch(
        reverse("bookings:event-detail", args=[event.pk]),
        {"title": "Kapret"},
        format="json",
    )
    assert response.status_code == 403


def test_the_owner_can_retitle_their_public_event(resident):
    event = make_event(resident, days_ahead=26, category=EventCategory.PUBLIC, title="Strikkecafé")
    response = as_user(resident).patch(
        reverse("bookings:event-detail", args=[event.pk]),
        {"title": "Strikke- og hæklecafé", "description": "Tag dit garn med."},
        format="json",
    )

    assert response.status_code == 200
    event.refresh_from_db()
    assert event.title == "Strikke- og hæklecafé"
    assert event.description == "Tag dit garn med."


def test_a_booking_cannot_be_moved_inside_the_notice_period(resident):
    """The edit route must not be a way round the rules on the create route."""
    event = make_event(resident, days_ahead=26)
    too_soon = timezone.now() + timedelta(days=3)
    response = as_user(resident).patch(
        reverse("bookings:event-detail", args=[event.pk]),
        {"start": too_soon.isoformat(), "end": (too_soon + timedelta(hours=2)).isoformat()},
        format="json",
    )

    assert response.status_code == 400
    assert "start" in response.json()
    event.refresh_from_db()
    assert event.start > too_soon


def test_a_move_that_clashes_is_a_field_error_not_a_server_error(resident, neighbour):
    taken = make_event(neighbour, days_ahead=30)
    event = make_event(resident, days_ahead=26)
    response = as_user(resident).patch(
        reverse("bookings:event-detail", args=[event.pk]),
        {"start": taken.start.isoformat(), "end": taken.end.isoformat()},
        format="json",
    )

    assert response.status_code == 400
    assert "start" in response.json()


def test_a_cancelled_booking_cannot_be_edited(resident):
    """Editing one would not reclaim its slot, so it must not look as if it had."""
    event = make_event(resident, days_ahead=26)
    event.cancel(by=resident)

    response = as_user(resident).patch(
        reverse("bookings:event-detail", args=[event.pk]),
        {"end": (event.end + timedelta(hours=1)).isoformat()},
        format="json",
    )
    assert response.status_code == 400


def test_the_calendar_says_who_may_edit_each_booking(resident, neighbour, admin_user):
    event = make_event(resident, days_ahead=24)
    url = reverse("bookings:event-list")

    assert as_user(resident).get(url).json()[0]["can_edit"] is True
    assert as_user(neighbour).get(url).json()[0]["can_edit"] is False
    assert as_user(admin_user).get(url).json()[0]["can_edit"] is True

    event.cancel(by=resident)
    cancelled = as_user(resident).get(url, {"include_cancelled": "true"}).json()[0]
    assert cancelled["can_edit"] is False


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
    event = make_event(resident, days_ahead=25)
    day = timezone.localtime(event.start).date()

    response = as_user(resident).get(
        reverse("bookings:event-list"), {"from": day.isoformat(), "to": day.isoformat()}
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [event.pk]


def test_events_outside_the_window_are_left_out(resident):
    event = make_event(resident, days_ahead=25)
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
    make_event(neighbour, days_ahead=24)

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
    event = make_event(neighbour, days_ahead=20)
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
    make_event(neighbour, days_ahead=29)  # sits on the second occurrence's week

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


def make_series(user, days_ahead=2, weeks=3, hours=3):
    """A weekly series whose evenings are all still to come."""
    start = timezone.now() + timedelta(days=days_ahead)
    series = EventSeries.objects.create(
        title="Strikkecafé",
        description="Tag dit garn med.",
        frequency=Frequency.WEEKLY,
        interval=1,
        until=(timezone.localtime(start) + timedelta(weeks=weeks)).date(),
        created_by=user,
        seed_start=start,
        seed_end=start + timedelta(hours=hours),
    )
    series.create_occurrences(start, start + timedelta(hours=hours))
    return series


def test_renaming_a_series_reaches_the_calendar(resident):
    series = make_series(resident)
    response = as_user(resident).patch(
        reverse("bookings:series-detail", args=[series.pk]),
        {"title": "Strikke- og hæklecafé"},
        format="json",
    )

    assert response.status_code == 200
    assert {event.title for event in series.occurrences.all()} == {"Strikke- og hæklecafé"}


def test_someone_else_cannot_change_your_series(resident, neighbour):
    series = make_series(resident)
    response = as_user(neighbour).patch(
        reverse("bookings:series-detail", args=[series.pk]),
        {"title": "Kapret"},
        format="json",
    )

    assert response.status_code == 403
    series.refresh_from_db()
    assert series.title == "Strikkecafé"


def test_retiming_a_series_moves_all_of_its_evenings(resident):
    series = make_series(resident)
    before = sorted(event.start for event in series.occurrences.all())

    response = as_user(resident).patch(
        reverse("bookings:series-detail", args=[series.pk]),
        {"start_shift_minutes": 30, "end_shift_minutes": 30},
        format="json",
    )

    assert response.status_code == 200
    after = sorted(event.start for event in series.occurrences.all())
    assert after == [moment + timedelta(minutes=30) for moment in before]


def test_a_retiming_that_clashes_changes_nothing(resident, neighbour):
    """All of it or none of it — a series meeting at two different times is
    worse than a refusal."""
    series = make_series(resident)
    second = series.occurrences.order_by("start")[1]
    Event.objects.create(
        category=EventCategory.PUBLIC,
        title="Beboermøde",
        start=second.end,
        end=second.end + timedelta(hours=2),
        created_by=neighbour,
    )
    before = sorted(event.start for event in series.occurrences.all())

    response = as_user(resident).patch(
        reverse("bookings:series-detail", args=[series.pk]),
        {"start_shift_minutes": 60, "end_shift_minutes": 60},
        format="json",
    )

    assert response.status_code == 400
    assert sorted(event.start for event in series.occurrences.all()) == before


def test_a_later_until_adds_evenings(resident):
    series = make_series(resident)
    response = as_user(resident).patch(
        reverse("bookings:series-detail", args=[series.pk]),
        {"until": (series.until + timedelta(days=14)).isoformat()},
        format="json",
    )

    assert response.status_code == 200
    assert series.occurrences.count() == 6


def test_an_earlier_until_cancels_the_evenings_beyond_it(resident):
    series = make_series(resident)
    response = as_user(resident).patch(
        reverse("bookings:series-detail", args=[series.pk]),
        {"until": (series.until - timedelta(days=10)).isoformat()},
        format="json",
    )

    assert response.status_code == 200
    assert series.occurrences.count() == 4  # kept, so sign-ups survive
    assert series.occurrences.filter(cancelled_at__isnull=False).count() == 2


def test_the_rhythm_of_a_series_cannot_be_changed(resident):
    """Changing how often it meets is a different series, not an edit."""
    series = make_series(resident)
    response = as_user(resident).patch(
        reverse("bookings:series-detail", args=[series.pk]),
        {"frequency": Frequency.DAILY, "interval": 3},
        format="json",
    )

    assert response.status_code == 200
    series.refresh_from_db()
    assert series.frequency == Frequency.WEEKLY
    assert series.interval == 1


def test_a_series_cannot_be_made_to_end_before_it_began(resident):
    series = make_series(resident)
    response = as_user(resident).patch(
        reverse("bookings:series-detail", args=[series.pk]),
        {"until": (timezone.localdate() - timedelta(days=30)).isoformat()},
        format="json",
    )

    assert response.status_code == 400
    assert "until" in response.json()


# --- the booking policy -----------------------------------------------------


def test_anyone_logged_in_can_read_the_policy(resident):
    response = as_user(resident).get(reverse("bookings:settings"))

    assert response.status_code == 200
    policy = response.json()
    assert policy["private_booking_min_notice_days"] == 14
    assert policy["private_booking_max_horizon_days"] == 90
    assert policy["private_booking_weekdays"] == [0, 1, 2, 3, 4, 5, 6]


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
