"""The reminder mailed the morning before a private booking."""

from datetime import datetime, time, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.bookings.models import BookingReminderEmail, Event, EventCategory

User = get_user_model()


def at(days_ahead, hour=18, hours=3):
    """`hour`:00 local time, that many days from today, and when it ends."""
    day = timezone.localdate() + timedelta(days=days_ahead)
    start = timezone.make_aware(datetime.combine(day, time(hour)))
    return start, start + timedelta(hours=hours)


@pytest.fixture
def booker(db):
    # Staff, so the notice period does not stop a booking for tomorrow.
    return User.objects.create_user(
        email="anna@example.dk", password="hemmeligt123", first_name="Anna", is_staff=True
    )


def book(user, days_ahead=1, category=EventCategory.PRIVATE, hour=18, **extra):
    start, end = at(days_ahead, hour)
    if category == EventCategory.PUBLIC:
        extra.setdefault("title", "Strikkecafé")
    return Event.objects.create(category=category, start=start, end=end, created_by=user, **extra)


def run(*args):
    call_command("send_booking_reminders", *args)


def test_a_private_booking_tomorrow_is_reminded(booker):
    event = book(booker)

    run()

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == ["anna@example.dk"]
    assert "Kære nabo!" in sent.body
    assert "18:00" in sent.body and "21:00" in sent.body
    event.refresh_from_db()
    assert event.reminder_sent_at is not None


def test_a_booking_is_reminded_only_once(booker):
    book(booker)

    run()
    run()

    assert len(mail.outbox) == 1


def test_the_mail_comes_in_html_as_well(booker):
    book(booker)

    run()

    html, content_type = mail.outbox[0].alternatives[0]
    assert content_type == "text/html"
    assert "Kære nabo!" in html


@pytest.mark.parametrize("days_ahead", [2, 20])
def test_only_tomorrows_bookings_are_reminded(booker, days_ahead):
    book(booker, days_ahead=days_ahead, hour=23)

    run()

    assert mail.outbox == []


def test_a_booking_that_has_already_started_is_not_reminded(booker):
    # Reached by moving the clock rather than the booking: the check is
    # `start > now`, so a start earlier than now is the way to see it.
    event = book(booker)
    Event.objects.filter(pk=event.pk).update(start=timezone.now() - timedelta(minutes=5))

    run()

    assert mail.outbox == []


def test_a_public_event_is_not_reminded(booker):
    book(booker, category=EventCategory.PUBLIC)

    run()

    assert mail.outbox == []


def test_a_cancelled_booking_is_not_reminded(booker):
    book(booker).cancel(by=booker)

    run()

    assert mail.outbox == []


def test_a_deactivated_account_is_not_reminded(booker):
    book(booker)
    User.objects.filter(pk=booker.pk).update(is_active=False)

    run()

    assert mail.outbox == []


def test_nothing_is_sent_while_reminders_are_switched_off(booker):
    BookingReminderEmail.objects.update_or_create(pk=1, defaults={"enabled": False})
    event = book(booker)

    run()

    assert mail.outbox == []
    event.refresh_from_db()
    assert event.reminder_sent_at is None


def test_a_dry_run_sends_and_marks_nothing(booker, capsys):
    event = book(booker)

    run("--dry-run")

    assert mail.outbox == []
    event.refresh_from_db()
    assert event.reminder_sent_at is None
    assert "1 påmindelse" in capsys.readouterr().out


def test_a_failed_send_is_retried_by_the_next_run(booker, monkeypatch):
    event = book(booker)

    def refuse(*args, **kwargs):
        raise OSError("smtp down")

    monkeypatch.setattr(
        "apps.bookings.management.commands.send_booking_reminders.send_booking_reminder_email",
        refuse,
    )
    with pytest.raises(CommandError):
        run()
    event.refresh_from_db()
    assert event.reminder_sent_at is None

    monkeypatch.undo()
    run()

    assert len(mail.outbox) == 1


def test_one_failed_send_does_not_stop_the_others(booker, monkeypatch):
    other = User.objects.create_user(
        email="bo@example.dk", password="hemmeligt123", first_name="Bo", is_staff=True
    )
    book(booker, hour=10)
    book(other, hour=18)
    from apps.bookings.management.commands import send_booking_reminders as command

    real = command.send_booking_reminder_email

    def refuse_anna(event, reminder):
        if event.created_by == booker:
            raise OSError("smtp down")
        real(event, reminder)

    monkeypatch.setattr(command, "send_booking_reminder_email", refuse_anna)

    with pytest.raises(CommandError):
        run()

    assert [m.to for m in mail.outbox] == [["bo@example.dk"]]


def test_moving_a_reminded_booking_to_another_day_owes_it_a_new_reminder(booker):
    event = book(booker)
    run()
    event = Event.objects.get(pk=event.pk)
    assert event.reminder_sent_at is not None

    event.start, event.end = at(2)
    event.save()

    event.refresh_from_db()
    assert event.reminder_sent_at is None


def test_retiming_a_reminded_booking_within_its_day_keeps_the_marker(booker):
    event = book(booker)
    run()
    event = Event.objects.get(pk=event.pk)

    event.start, event.end = at(1, hour=19)
    event.save()

    event.refresh_from_db()
    assert event.reminder_sent_at is not None


def test_the_text_the_board_wrote_is_what_gets_sent(booker):
    reminder = BookingReminderEmail.load()
    reminder.subject = "I morgen, {navn}"
    reminder.body = "Kl. {start}–{slut}.\n\nHusk nøglen & lyset."
    reminder.save()
    book(booker)

    run()

    sent = mail.outbox[0]
    assert sent.subject == "I morgen, Anna"
    assert "Husk nøglen & lyset." in sent.body
    assert "Husk nøglen &amp; lyset." in sent.alternatives[0][0]


def test_markup_in_the_text_is_not_passed_on_as_html(booker):
    reminder = BookingReminderEmail.load()
    reminder.body = "<script>alert(1)</script>"
    reminder.save()
    book(booker)

    run()

    assert "<script>" not in mail.outbox[0].alternatives[0][0]


def test_an_unknown_placeholder_is_refused(db):
    reminder = BookingReminderEmail.load()
    reminder.body = "Hej {fornavn}"

    with pytest.raises(ValidationError) as error:
        reminder.full_clean()

    assert "body" in error.value.message_dict
    assert "{fornavn}" in error.value.message_dict["body"][0]


def test_the_default_text_uses_only_known_placeholders(db):
    BookingReminderEmail.load().full_clean()


def test_the_date_is_written_in_danish(booker):
    event = book(booker)

    day = BookingReminderEmail.values_for(event)["dato"]

    assert day.startswith(("mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"))


def test_a_heading_is_set_apart_from_the_paragraph_under_it():
    from apps.accounts.emails import _reminder_blocks

    blocks = _reminder_blocks(
        "Kære nabo!\n\n## Generelt\nFørste linje\nAnden linje\n\nNæste afsnit"
    )

    assert blocks == [
        {"heading": False, "text": "Kære nabo!"},
        {"heading": True, "text": "Generelt"},
        {"heading": False, "text": "Første linje\nAnden linje"},
        {"heading": False, "text": "Næste afsnit"},
    ]
