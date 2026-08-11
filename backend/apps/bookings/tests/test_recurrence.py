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
