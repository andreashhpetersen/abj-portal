"""
Mail everyone with a private booking tomorrow a reminder.

Run it once a day at 10:00 (see OPERATIONS.md). It is safe to run at any moment
and any number of times: each booking is claimed with `reminder_sent_at` before
its mail goes out, so a second run — or two at once — sends nothing twice, and a
mail that fails to send releases its claim so the next run tries again.

    python manage.py send_booking_reminders
    python manage.py send_booking_reminders --dry-run   # list who would be mailed

A booking placed after 10:00 the day before gets no reminder: residents must
book a fortnight ahead, so that only happens to bookings an admin places or
moves at short notice, and they have just been told.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.emails import send_booking_reminder_email
from apps.bookings.models import BookingReminderEmail, Event, EventCategory


def due_events(now=None):
    """Live private bookings that start tomorrow and have not been reminded."""
    now = now or timezone.now()
    tomorrow = timezone.localdate(now) + timedelta(days=1)
    return (
        Event.objects.filter(
            category=EventCategory.PRIVATE,
            cancelled_at__isnull=True,
            reminder_sent_at__isnull=True,
            start__date=tomorrow,
            start__gt=now,
            created_by__is_active=True,
        )
        .exclude(created_by__email="")
        .select_related("created_by")
    )


class Command(BaseCommand):
    help = "Send påmindelsen til dem, der har booket beboerlokalet privat i morgen."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the bookings that would be reminded; send and mark nothing.",
        )

    def handle(self, *args, **options):
        reminder = BookingReminderEmail.load()
        if not reminder.enabled:
            self.stdout.write("Påmindelser er slået fra — intet at gøre.")
            return

        events = list(due_events())

        if options["dry_run"]:
            self.stdout.write(f"Tørkørsel: {len(events)} påmindelse(r) ville blive sendt.")
            for event in events:
                self.stdout.write(f"  {event.created_by.email} — {event}")
            return

        sent = failed = 0
        for event in events:
            # The claim is the idempotency: only whoever flips the column from
            # NULL gets to send, so an overlapping run skips this booking.
            claimed = Event.objects.filter(pk=event.pk, reminder_sent_at__isnull=True).update(
                reminder_sent_at=timezone.now()
            )
            if not claimed:
                continue
            try:
                send_booking_reminder_email(event, reminder)
            except Exception as error:  # one bad address must not stop the rest
                Event.objects.filter(pk=event.pk).update(reminder_sent_at=None)
                failed += 1
                self.stderr.write(
                    self.style.ERROR(f"Kunne ikke sende til {event.created_by.email}: {error}")
                )
            else:
                sent += 1

        self.stdout.write(self.style.SUCCESS(f"{sent} påmindelse(r) sendt, {failed} fejlede."))
        if failed:
            raise CommandError(f"{failed} påmindelse(r) blev ikke sendt og prøves igen.")
