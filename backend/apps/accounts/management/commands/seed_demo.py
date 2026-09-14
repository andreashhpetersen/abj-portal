"""
Fill a development database with plausible Danish demo data.

Refuses to run unless DEBUG is on: the accounts it creates have known passwords,
which must never exist in a real deployment.

    python manage.py seed_demo [--reset]
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import ERHVERVSUDVALG_GROUP, Building, Resident
from apps.bookings.models import (
    BookingSettings,
    Event,
    EventAttendance,
    EventCategory,
    EventSeries,
    Frequency,
)
from apps.shoprentals.ingest import parse_rows, sync_sheet
from apps.shoprentals.models import Application, ApplicationComment, ApplicationStatus

User = get_user_model()

PASSWORD = "beboer1234"
DEMO_DOMAIN = "@example.dk"


def at(days_ahead, hour, minute=0):
    """A local time that many days from now, as an aware datetime."""
    day = timezone.localtime(timezone.now()) + timedelta(days=days_ahead)
    naive = day.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=None)
    return timezone.make_aware(naive, timezone.get_current_timezone())


class Command(BaseCommand):
    help = "Create demo users, buildings and bookings for local development."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help=f"Delete existing demo data first (anything on {DEMO_DOMAIN}).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "seed_demo only runs with DEBUG=True — it creates accounts with "
                "publicly known passwords."
            )

        if options["reset"]:
            self._reset()

        if User.objects.filter(email__endswith=DEMO_DOMAIN).exists():
            self.stdout.write(
                self.style.WARNING("Demo data already present. Use --reset to rebuild it.")
            )
            return

        buildings = self._create_buildings()
        people = self._create_people(buildings)
        self._create_bookings(people)
        self._create_applications(people)

        BookingSettings.load()

        self.stdout.write(self.style.SUCCESS("\nDemo data created. Log in with:\n"))
        for email, role in [
            ("formand@example.dk", "admin — sees everything, can cancel anyone's booking"),
            ("andreas@example.dk", "resident, on the erhvervsudvalg"),
            ("mette@example.dk", "resident"),
            ("vicevaert@example.dk", "employee — no residency"),
        ]:
            self.stdout.write(f"  {email:<24} {PASSWORD}   ({role})")

    def _reset(self):
        # Only the demo applications. Deleting every one would destroy real
        # applications synced from the form, and the committee's work on them.
        # Demo-authored comments go first and by author, because a demo user who
        # commented on a real application would otherwise block their own
        # deletion — ApplicationComment.author is PROTECT.
        ApplicationComment.objects.filter(author__email__endswith=DEMO_DOMAIN).delete()
        Application.objects.filter(source_key__in=demo_application_keys()).delete()
        EventAttendance.objects.all().delete()
        Event.objects.all().delete()
        EventSeries.objects.all().delete()
        Resident.objects.all().delete()
        Building.objects.all().delete()
        User.objects.filter(email__endswith=DEMO_DOMAIN).delete()
        self.stdout.write("Existing demo data removed.")

    def _create_buildings(self):
        return [
            Building.objects.create(street="Sankt Knuds Vej", house_number="12", name="Blok A"),
            Building.objects.create(street="Sankt Knuds Vej", house_number="14", name="Blok A"),
            Building.objects.create(street="Vesterbrogade", house_number="3", name="Blok B"),
        ]

    def _create_people(self, buildings):
        chair = User.objects.create_user(
            email="formand@example.dk",
            password=PASSWORD,
            first_name="Bente",
            last_name="Sørensen",
            phone="+45 20 11 22 33",
            is_staff=True,
            is_superuser=True,
        )
        andreas = User.objects.create_user(
            email="andreas@example.dk",
            password=PASSWORD,
            first_name="Andreas",
            last_name="Holck",
            phone="+45 12 34 56 78",
        )
        mette = User.objects.create_user(
            email="mette@example.dk",
            password=PASSWORD,
            first_name="Mette",
            last_name="Nielsen",
        )
        caretaker = User.objects.create_user(
            email="vicevaert@example.dk",
            password=PASSWORD,
            first_name="Jørgen",
            last_name="Dam",
            phone="+45 40 55 66 77",
        )

        # The chair and the two residents live here; the caretaker does not,
        # which is the case the Resident model exists to handle.
        for index, (user, building, floor, door) in enumerate(
            [
                (chair, buildings[0], "1", "th"),
                (andreas, buildings[0], "3", "tv"),
                (mette, buildings[2], "st.", ""),
            ]
        ):
            Resident.objects.create(
                user=user,
                unit_number=f"1-2345-678{index}",
                resident_number=f"1-2345-678{index}-2",
                building=building,
                floor=floor,
                door=door,
            )

        committee, _created = Group.objects.get_or_create(name=ERHVERVSUDVALG_GROUP)
        andreas.groups.add(committee)

        return {"chair": chair, "andreas": andreas, "mette": mette, "caretaker": caretaker}

    def _create_bookings(self, people):
        # Spread out so nothing clashes. Residents' private bookings sit inside
        # the window they are held to: at least 14 days' notice, at most 90.
        Event.objects.create(
            category=EventCategory.PRIVATE,
            start=at(16, 17),
            end=at(16, 23),
            created_by=people["mette"],
        )
        Event.objects.create(
            category=EventCategory.PRIVATE,
            start=at(22, 12),
            end=at(22, 16),
            created_by=people["andreas"],
        )

        # Close in and running past midnight: only an admin may book this, and
        # it shows on two days in the calendar.
        Event.objects.create(
            category=EventCategory.PRIVATE,
            start=at(12, 19),
            end=at(13, 2),
            created_by=people["chair"],
        )

        party = Event.objects.create(
            category=EventCategory.PUBLIC,
            title="Sommerfest i gården",
            description="Fælles grill. Tag en ret med, foreningen giver drikkevarer.",
            start=at(6, 15),
            end=at(6, 23),
            created_by=people["chair"],
        )
        for guest in (people["andreas"], people["mette"], people["caretaker"]):
            EventAttendance.objects.create(event=party, user=guest)

        meeting = Event.objects.create(
            category=EventCategory.PUBLIC,
            title="Beboermøde om gårdrenovering",
            description="Kom og hør om planerne for den nye gård.",
            start=at(20, 19),
            end=at(20, 21),
            created_by=people["chair"],
        )
        EventAttendance.objects.create(event=meeting, user=people["mette"])

        cafe = EventSeries.objects.create(
            title="Brætspilscafé",
            description="Alle er velkomne. Vi har spillene, du tager kaffen.",
            frequency=Frequency.WEEKLY,
            interval=1,
            until=(timezone.localtime(timezone.now()) + timedelta(days=45)).date(),
            created_by=people["andreas"],
        )
        cafe.create_occurrences(at(4, 19), at(4, 22))

        # One cancelled booking, so the freed-slot behaviour is visible.
        cancelled = Event.objects.create(
            category=EventCategory.PRIVATE,
            start=at(25, 10),
            end=at(25, 14),
            created_by=people["mette"],
        )
        cancelled.cancel(by=people["mette"])

    def _create_applications(self, people):
        """Shop-rental applications, seeded through the real ingest path.

        Deliberately routed through `sync_sheet` rather than creating rows
        directly: it exercises the header mapping, so the demo data proves the
        ingestion works and the page has something to show without anyone having
        to set up a Google service account first.
        """
        sync_sheet(DEMO_APPLICATION_HEADER, DEMO_APPLICATION_ROWS)

        # Scoped to the demo rows. A developer may well have a real sheet synced
        # into the same database, and those applications are not ours to restyle.
        applications = list(
            Application.objects.filter(source_key__in=demo_application_keys()).order_by(
                "submitted_at"
            )
        )

        # Spread across the workflow so the filter chips and the "how long has
        # this been standing still" line both have something to show.
        plan = [
            (ApplicationStatus.FINISHED, 5, "chair"),
            (ApplicationStatus.IN_PROGRESS, 4, "andreas"),
            (ApplicationStatus.REJECTED, 1, None),
            (ApplicationStatus.SAVED, 4, "andreas"),
            (ApplicationStatus.REJECTED, 2, None),
            (ApplicationStatus.NEW, None, None),
        ]
        for application, (status, rating, owner) in zip(applications, plan, strict=False):
            application.set_status(status, save=False)
            application.rating = rating
            application.assignee = people[owner] if owner else None
            application.save()

        by_email = {application.email: application for application in applications}
        for email, author, body in DEMO_APPLICATION_COMMENTS:
            if email in by_email:
                ApplicationComment.objects.create(
                    application=by_email[email], author=people[author], body=body
                )


#: The demo form. A plausible shape for the association's own — including a
#: multi-line question, since every heading in the real form has a description
#: under its title, and a question the aliases do not know, since the form is
#: expected to grow. Module level so `--reset` can identify exactly these rows.
DEMO_APPLICATION_HEADER = [
    "Tidsstempel",
    "Navn\n(Fornavn(e) + Efternavn)",
    "Mailadresse",
    "Telefonnummer",
    "Hvad vil du bruge lejemålet til?",
    "Hvor mange m² har du brug for?",
    "Fortæl kort om dig selv og din virksomhed",
]

DEMO_APPLICATION_ROWS = [
    [
        "02/08/2026 09.14.22",
        "Mette Sørensen",
        "mette@blomsterhjoernet.dk",
        "+45 22 33 44 55",
        "Blomsterbutik",
        "60-80",
        "Jeg har drevet blomsterbutik på Vesterbro i tolv år og leder efter "
        "et mindre lejemål med gadeplan.",
    ],
    [
        "05/08/2026 17.42.03",
        "Kasper Lund",
        "kasper@lundkaffe.dk",
        "+45 26 11 09 88",
        "Kaffebar med enkelt madudvalg",
        "45",
        "Vi er to, der vil åbne vores første kaffebar. Erfaring fra Coffee Collective.",
    ],
    [
        "09/08/2026 21.05.51",
        "Ahmed Karim",
        "ahmed@karimtandpleje.dk",
        "+45 31 88 21 40",
        "Tandlægeklinik",
        "120",
        "Etableret klinik i Valby, som skal udvide med en afdeling mere.",
    ],
    [
        "11/08/2026 08.31.17",
        "Sofie Bang",
        "sofie@bangyoga.dk",
        "+45 28 74 63 12",
        "Yogastudie",
        "90",
        "Underviser i dag på lejede timer og vil gerne have egne lokaler.",
    ],
    [
        "14/08/2026 13.58.44",
        "Henrik Toft",
        "kontakt@toftvvs.dk",
        "+45 40 12 76 55",
        "Lager til VVS-firma",
        "150",
        "Har brug for lager og et lille kontor. Ingen kundebetjening fra adressen.",
    ],
    [
        "18/08/2026 11.02.09",
        "Line Damgaard",
        "line@damgaardkeramik.dk",
        "+45 23 45 67 89",
        "Keramikværksted med butik",
        "70",
        "Værksted forrest og lille butik ud mod gaden. Åbent tre dage om ugen.",
    ],
]

#: (applicant email, demo user key, comment body).
DEMO_APPLICATION_COMMENTS = [
    (
        "kasper@lundkaffe.dk",
        "andreas",
        "Ringet 6/8. Rigtig god snak — de vil gerne se lejemålet i Jægergade.",
    ),
    (
        "kasper@lundkaffe.dk",
        "chair",
        "Husk at spørge om de har finansieringen på plads inden vi går videre.",
    ),
    (
        "sofie@bangyoga.dk",
        "andreas",
        "Ingen ledige lokaler i den størrelse lige nu. Gemt — hun er et godt match, "
        "så vi tager fat når kælderen bliver fri.",
    ),
]


def demo_application_keys():
    """Source keys of the demo applications, derived exactly as the sync does.

    This is what lets `--reset` delete the demo rows and nothing else. It matters
    as soon as a real responses sheet has been synced into the same database:
    wiping every application would take the committee's ratings, statuses and
    comments with it, and those do not come back.
    """
    responses, _skipped = parse_rows(DEMO_APPLICATION_HEADER, DEMO_APPLICATION_ROWS)
    return [response.source_key for response in responses]
