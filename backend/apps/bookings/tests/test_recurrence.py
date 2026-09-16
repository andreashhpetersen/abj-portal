"""Recurring public events.

`occurrence_times` is a pure generator, so most of this exercises it directly
with fixed dates — no database, and no dependence on today's date.
"""

import datetime as dt
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.bookings.models import MAX_OCCURRENCES, EventSeries, Frequency

User = get_user_model()


def local(year, month, day, hour=19):
    return timezone.make_aware(dt.datetime(year, month, day, hour), timezone.get_current_timezone())


def series(frequency, until, interval=1):
    """An unsaved series — enough to expand a pattern."""
    return EventSeries(title="Brætspilscafé", frequency=frequency, interval=interval, until=until)


def test_a_weekly_series_repeats_every_seven_days():
    start = local(2026, 10, 6)
    occurrences = list(
        series(Frequency.WEEKLY, dt.date(2026, 10, 27)).occurrence_times(
            start, start + timedelta(hours=3)
        )
    )
    assert [timezone.localtime(begin).date() for begin, _end in occurrences] == [
        dt.date(2026, 10, 6),
        dt.date(2026, 10, 13),
        dt.date(2026, 10, 20),
        dt.date(2026, 10, 27),
    ]


def test_an_interval_of_two_means_every_second_week():
    start = local(2026, 10, 6)
    occurrences = list(
        series(Frequency.WEEKLY, dt.date(2026, 11, 4), interval=2).occurrence_times(
            start, start + timedelta(hours=3)
        )
    )
    assert [timezone.localtime(begin).date() for begin, _end in occurrences] == [
        dt.date(2026, 10, 6),
        dt.date(2026, 10, 20),
        dt.date(2026, 11, 3),
    ]


def test_the_series_stops_at_the_until_date():
    start = local(2026, 10, 6)
    occurrences = list(
        series(Frequency.WEEKLY, dt.date(2026, 10, 12)).occurrence_times(
            start, start + timedelta(hours=3)
        )
    )
    assert len(occurrences) == 1


def test_a_weekly_event_keeps_its_local_time_across_the_clock_change():
    """Denmark leaves summer time on 25 October 2026.

    Naive UTC arithmetic would drag a 19:00 event to 20:00 afterwards.
    """
    start = local(2026, 10, 21, hour=19)
    occurrences = list(
        series(Frequency.WEEKLY, dt.date(2026, 11, 5)).occurrence_times(
            start, start + timedelta(hours=3)
        )
    )

    assert len(occurrences) == 3
    assert [timezone.localtime(begin).hour for begin, _end in occurrences] == [19, 19, 19]
    # The clock change really did happen inside the series.
    assert (
        timezone.localtime(occurrences[0][0]).utcoffset()
        != timezone.localtime(occurrences[-1][0]).utcoffset()
    )


def test_every_occurrence_keeps_the_same_duration_across_the_clock_change():
    start = local(2026, 10, 21)
    occurrences = list(
        series(Frequency.WEEKLY, dt.date(2026, 11, 5)).occurrence_times(
            start, start + timedelta(hours=3)
        )
    )
    assert all(end - begin == timedelta(hours=3) for begin, end in occurrences)


def test_a_monthly_series_clamps_to_the_end_of_short_months():
    start = local(2026, 1, 31)
    occurrences = list(
        series(Frequency.MONTHLY, dt.date(2026, 4, 30)).occurrence_times(
            start, start + timedelta(hours=2)
        )
    )
    assert [timezone.localtime(begin).date() for begin, _end in occurrences] == [
        dt.date(2026, 1, 31),
        dt.date(2026, 2, 28),
        dt.date(2026, 3, 31),
        dt.date(2026, 4, 30),
    ]


def test_a_daily_series_is_capped_so_it_cannot_run_away():
    start = local(2026, 1, 1)
    occurrences = list(
        series(Frequency.DAILY, dt.date(2036, 1, 1)).occurrence_times(
            start, start + timedelta(hours=1)
        )
    )
    assert len(occurrences) == MAX_OCCURRENCES


@pytest.fixture
def organiser(db):
    return User.objects.create_user(email="udvalg@example.dk", password="hemmeligt123")


def test_creating_a_series_materialises_real_events(organiser):
    start = timezone.now() + timedelta(days=2)
    end = start + timedelta(hours=3)
    saved = EventSeries.objects.create(
        title="Brætspilscafé",
        description="Kom og spil.",
        frequency=Frequency.WEEKLY,
        until=(timezone.localtime(start) + timedelta(days=21)).date(),
        created_by=organiser,
    )

    occurrences = saved.create_occurrences(start, end)

    assert len(occurrences) == 4
    assert saved.occurrences.count() == 4
    assert all(event.is_public for event in occurrences)
    assert all(event.title == "Brætspilscafé" for event in occurrences)


def test_cancelling_one_occurrence_leaves_the_rest_alone(organiser):
    start = timezone.now() + timedelta(days=2)
    saved = EventSeries.objects.create(
        title="Brætspilscafé",
        frequency=Frequency.WEEKLY,
        until=(timezone.localtime(start) + timedelta(days=14)).date(),
        created_by=organiser,
    )
    occurrences = saved.create_occurrences(start, start + timedelta(hours=3))

    occurrences[1].cancel(by=organiser)

    assert [event.is_cancelled for event in saved.occurrences.order_by("start")] == [
        False,
        True,
        False,
    ]


# --- changing a series after the fact ----------------------------------------


@pytest.fixture
def weekly(organiser, db):
    """A weekly café, four evenings, all of them still to come."""
    start = timezone.now() + timedelta(days=2)
    saved = EventSeries.objects.create(
        title="Strikkecafé",
        description="Tag dit garn med.",
        frequency=Frequency.WEEKLY,
        until=(timezone.localtime(start) + timedelta(days=21)).date(),
        created_by=organiser,
        seed_start=start,
        seed_end=start + timedelta(hours=3),
    )
    saved.create_occurrences(start, start + timedelta(hours=3))
    return saved


def titles(series_row):
    return [event.title for event in series_row.occurrences.order_by("start")]


def test_renaming_a_series_renames_its_upcoming_occurrences(weekly):
    was_titled, was_described = weekly.title, weekly.description
    weekly.title = "Strikke- og hæklecafé"
    weekly.save()

    weekly.retitle_occurrences(was_titled, was_described)

    assert titles(weekly) == ["Strikke- og hæklecafé"] * 4


def test_an_occurrence_renamed_by_hand_keeps_its_own_title(weekly):
    """It says something the series does not — a series-wide rename is no
    reason to lose it."""
    special = weekly.occurrences.order_by("start")[2]
    special.title = "Strikkecafé med julehygge"
    special.save()

    was_titled, was_described = weekly.title, weekly.description
    weekly.title = "Strikke- og hæklecafé"
    weekly.save()
    weekly.retitle_occurrences(was_titled, was_described)

    assert titles(weekly) == [
        "Strikke- og hæklecafé",
        "Strikke- og hæklecafé",
        "Strikkecafé med julehygge",
        "Strikke- og hæklecafé",
    ]


def test_a_cancelled_occurrence_is_left_out_of_a_series_change(weekly):
    cancelled = weekly.occurrences.order_by("start")[1]
    cancelled.cancel(by=weekly.created_by)

    was_titled, was_described = weekly.title, weekly.description
    weekly.title = "Strikke- og hæklecafé"
    weekly.save()
    weekly.retitle_occurrences(was_titled, was_described)

    cancelled.refresh_from_db()
    assert cancelled.title == "Strikkecafé"
    assert cancelled.is_cancelled is True


def test_retiming_a_series_moves_every_upcoming_occurrence(weekly):
    before = [timezone.localtime(event.start) for event in weekly.occurrences.order_by("start")]

    weekly.shift_occurrences(timedelta(minutes=30), timedelta(minutes=30))

    after = [timezone.localtime(event.start) for event in weekly.occurrences.order_by("start")]
    assert [moment.hour * 60 + moment.minute for moment in after] == [
        moment.hour * 60 + moment.minute + 30 for moment in before
    ]


def test_retiming_a_series_moves_the_pattern_with_it(weekly):
    """Otherwise extending the series later would put the new evenings back at
    the hour the series no longer meets at."""
    was = timezone.localtime(weekly.seed_start)

    weekly.shift_occurrences(timedelta(minutes=30), timedelta(minutes=30))

    weekly.refresh_from_db()
    assert timezone.localtime(weekly.seed_start) == was + timedelta(minutes=30)


def test_an_occurrence_moved_by_hand_moves_with_the_series(weekly):
    """A delta, not a recomputed time: the evening somebody moved to another
    day stays moved, and still meets half an hour later like the rest."""
    moved = weekly.occurrences.order_by("start")[2]
    moved.start += timedelta(days=1)
    moved.end += timedelta(days=1)
    moved.save()
    was = moved.start

    weekly.shift_occurrences(timedelta(minutes=30), timedelta(minutes=30))

    moved.refresh_from_db()
    assert moved.start == was + timedelta(minutes=30)


def test_a_later_until_materialises_the_evenings_it_reaches(weekly):
    weekly.until = weekly.until + timedelta(days=14)
    weekly.save()

    created = weekly.extend()

    assert len(created) == 2
    assert weekly.occurrences.count() == 6


def test_extending_a_series_does_not_refill_a_gap(weekly):
    """A cancelled evening is a decision somebody made, not a hole to plug."""
    weekly.occurrences.order_by("start")[1].cancel(by=weekly.created_by)
    weekly.until = weekly.until + timedelta(days=7)
    weekly.save()

    weekly.extend()

    assert weekly.occurrences.count() == 5
    assert [event.is_cancelled for event in weekly.occurrences.order_by("start")] == [
        False,
        True,
        False,
        False,
        False,
    ]


def test_an_earlier_until_cancels_the_evenings_it_no_longer_covers(weekly):
    weekly.until = weekly.until - timedelta(days=10)
    weekly.save()

    cancelled = weekly.cancel_beyond(by=weekly.created_by)

    assert len(cancelled) == 2
    assert [event.is_cancelled for event in weekly.occurrences.order_by("start")] == [
        False,
        False,
        True,
        True,
    ]


def test_shortening_a_series_keeps_the_evenings_rather_than_deleting_them(weekly):
    """People may have signed up, and the attendance rows would go with them."""
    weekly.until = weekly.until - timedelta(days=10)
    weekly.save()
    weekly.cancel_beyond(by=weekly.created_by)

    assert weekly.occurrences.count() == 4


def test_a_past_occurrence_is_not_touched_by_a_series_change(organiser, weekly):
    """An evening that has happened is a record of what happened."""
    past = weekly.occurrences.order_by("start").first()
    Event = past.__class__
    Event.objects.filter(pk=past.pk).update(
        start=timezone.now() - timedelta(days=7), end=timezone.now() - timedelta(days=7, hours=-3)
    )

    was_titled, was_described = weekly.title, weekly.description
    weekly.title = "Strikke- og hæklecafé"
    weekly.save()
    weekly.retitle_occurrences(was_titled, was_described)

    past.refresh_from_db()
    assert past.title == "Strikkecafé"
