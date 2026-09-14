"""
Read a resident-register export from INNA and reconcile it with the database.

INNA has no API yet, so the file arrives by hand: a board member downloads the
CSV from INNA's web interface, drops it in `backend/data/register/` — which is
gitignored, because a dump is six hundred people's names, emails and home
addresses — and runs this.

    python manage.py import_residents data/register/users-2026-09-08.csv --dry-run
    python manage.py import_residents data/register/users-2026-09-08.csv

Safe to run at any moment and any number of times. Each run reads the whole
export, so an interrupted run, a month of nobody bothering, or a correction INNA
made last week all sort themselves out on the next pass. Nothing is ever
deleted: somebody who has stopped appearing is flagged, and the board decides.

Besides the register itself the run does two things that follow from it, and
reports both rather than doing them quietly:

* Pending signups that the register now vouches for are approved. Somebody who
  signed up the week before their flat reached the export should not wait in the
  queue for a human to notice that it since arrived.
* Users who have no residency but whose email matches exactly one eligible
  entry get one attached. These are accounts a board member created by hand
  before the register existed; the address they were missing is now known.

When the export is one INNA has changed the shape of, `--dry-run` first: it
reports the columns it did not recognise, the addresses it could not read, and
the lines it had to skip, and writes nothing.
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import RegisterEntry, User
from apps.accounts.register import (
    ALIAS_TO_FIELD,
    attach_residency,
    auto_approve,
    import_rows,
    normalise_header,
    parse_rows,
    pending_requests_to_approve,
    unclassified_rows,
)

#: Columns without which the export cannot be used at all. Everything else is
#: optional — a missing `Tlf. Nr.` costs a phone number, a missing `Bolignr.`
#: costs the identity of every row in the file.
REQUIRED_FIELDS = ("unit_number", "raw_address", "resident_number", "unit_type", "role")


class Command(BaseCommand):
    help = "Importér beboerregistret fra en CSV-fil hentet hos INNA."

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            type=Path,
            help="The downloaded CSV, e.g. data/register/users-2026-09-08.csv",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Read and parse the file, report what would happen, write nothing.",
        )
        parser.add_argument(
            "--encoding",
            default="utf-8-sig",
            help="Override the file encoding. INNA exports UTF-8; older ones were cp1252.",
        )
        parser.add_argument(
            "--delimiter",
            default=",",
            help="Override the column separator, in case an export arrives semicolon-separated.",
        )

    def handle(self, *args, **options):
        header, rows = self._read(options["path"], options["encoding"], options["delimiter"])
        if not header:
            raise CommandError("Filen er tom — der er ingen kolonneoverskrifter at læse.")

        self._report_unmapped(header)
        parsed, skipped = parse_rows(header, rows)
        if not parsed:
            raise CommandError(
                "Ingen brugbare rækker. Tjek at det er beboerudtrækket og ikke en anden fil."
            )

        if options["dry_run"]:
            eligible = sum(1 for row in parsed if row.is_eligible)
            unreadable = [row for row in parsed if row.address is None]
            self.stdout.write(
                f"Tørkørsel: {len(parsed)} rækker læst, {eligible} kan aktivere en konto, "
                f"{len(skipped)} linjer sprunget over."
            )
            self._warn_skipped(skipped)
            self._warn_unparsed([(row.source_row, row.raw_address) for row in unreadable])
            self._warn_unclassified(unclassified_rows(parsed))
            return

        result = import_rows(parsed, skipped)
        self.stdout.write(
            self.style.SUCCESS(
                f"{result.created} nye, {result.updated} opdaterede, "
                f"{result.unchanged} uændrede ({result.total} i alt). "
                f"{RegisterEntry.objects.eligible().count()} kan aktivere en konto."
            )
        )
        if result.returned:
            self.stdout.write(f"{result.returned} række(r) er dukket op i registret igen.")
        if result.departed:
            self.stdout.write(
                self.style.WARNING(
                    f"{result.departed} række(r) står ikke længere i registret og er markeret "
                    "som fraflyttet. Deres konti er urørte — se dem efter i admin, og "
                    "deaktivér dem, der er flyttet."
                )
            )
        if result.buildings_created:
            self.stdout.write(
                self.style.WARNING(
                    "Nye opgange oprettet: "
                    + ", ".join(result.buildings_created)
                    + ". Tjek at de er rigtige og ikke en stavefejl i en adresse."
                )
            )

        self._approve_pending()
        self._link_existing_users()
        self._warn_skipped(result.skipped_rows)
        self._warn_unparsed(result.unparsed_addresses)
        self._warn_unclassified(result.unclassified)

    def _read(self, path, encoding, delimiter):
        """Pull the header and rows out of the CSV, and nothing more.

        All the meaning lives in `register.py`, which never sees a file. This is
        the whole of the portal's knowledge that the register arrives as CSV,
        which is what makes it cheap to swap for an API call the day INNA
        provides one.
        """
        if not path.exists():
            raise CommandError(f"Filen findes ikke: {path}")
        try:
            with path.open(newline="", encoding=encoding) as handle:
                rows = list(csv.reader(handle, delimiter=delimiter))
        except UnicodeDecodeError as error:
            raise CommandError(
                f"Kunne ikke læse {path} som {encoding}. Prøv --encoding cp1252."
            ) from error
        if not rows:
            return [], []
        return rows[0], rows[1:]

    def _approve_pending(self):
        """Let through anyone the register has caught up with."""
        approved = []
        for signup_request in pending_requests_to_approve():
            if auto_approve(signup_request) is not None:
                approved.append(signup_request.email)
        if approved:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{len(approved)} ventende tilmelding(er) godkendt automatisk, "
                    "fordi registret nu kender dem: " + ", ".join(approved)
                )
            )

    @transaction.atomic
    def _link_existing_users(self):
        """Give a residency to accounts that predate the register.

        Matched on email and only where exactly one eligible entry has it, and
        only for users who have no `Resident` row at all — a residency somebody
        corrected by hand is not this command's to overwrite. These accounts
        were created by a board member, so the email is one a human already
        vouched for; what was missing was an address to put beside it.
        """
        linked = []
        for user in User.objects.filter(resident__isnull=True).exclude(email=""):
            matches = list(RegisterEntry.objects.eligible().filter(email__iexact=user.email)[:2])
            # Two entries sharing an address is one person listed twice under
            # two units, which is a question for the board and not an address to
            # guess at.
            if len(matches) != 1 or attach_residency(user, matches[0]) is None:
                continue
            linked.append(user.email)
        if linked:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{len(linked)} eksisterende konto(er) har fået tilknyttet en bolig: "
                    + ", ".join(linked)
                )
            )

    def _warn_skipped(self, skipped):
        """Say so loudly. A silently dropped resident is the worst outcome."""
        if not skipped:
            return
        lines = ", ".join(str(line) for line in skipped)
        self.stderr.write(
            self.style.WARNING(
                f"Sprang linje(r) {lines} over: intet brugbart bolignr., "
                "så rækken kan ikke få en stabil identitet. "
                "Viceværterne står sådan i registret og hører hjemme i admin i stedet."
            )
        )

    def _warn_unparsed(self, unparsed):
        """Report addresses that could not be read into a flat.

        Expected for storage rooms, which is most of what turns up here. Worth
        reading anyway: a flat in this list is somebody who cannot activate an
        account, and the fix is a line in `FLOOR_ALIASES` or `DOOR_ALIASES`.
        """
        if not unparsed:
            return
        shown = ", ".join(f"linje {line}: {address!r}" for line, address in unparsed[:10])
        more = f" (+{len(unparsed) - 10} flere)" if len(unparsed) > 10 else ""
        self.stderr.write(
            self.style.WARNING(
                f"{len(unparsed)} adresse(r) kunne ikke læses som en bolig og kan derfor "
                f"ikke aktivere en konto: {shown}{more}. "
                "Kælder- og depotrum hører til her; en lejlighed gør ikke."
            )
        )

    def _warn_unclassified(self, unclassified):
        """Report flats the rule did not recognise as flats.

        Usually a row whose `Enhedstype` INNA left blank — a handful exist, and
        the people in them simply wait for the board. A long list means the
        column has been renamed and the import has stopped recognising anybody;
        that is the failure this warning exists for, because it otherwise looks
        exactly like a successful run.
        """
        if not unclassified:
            return
        shown = "; ".join(
            f"linje {line}: {name} ({unit_type or 'ingen enhedstype'}), {address}"
            for line, name, unit_type, address in unclassified[:10]
        )
        more = f" (+{len(unclassified) - 10} flere)" if len(unclassified) > 10 else ""
        self.stderr.write(
            self.style.WARNING(
                f"{len(unclassified)} række(r) har beboernr. og en læsbar boligadresse, men "
                f"tæller ikke som en bolig: {shown}{more}. "
                "De kan stadig tilmelde sig — deres anmodning venter bare på bestyrelsen. "
                "Er listen lang, har registret skiftet navn på enhedstyperne."
            )
        )

    def _report_unmapped(self, header):
        """Warn when the export no longer supplies a column the import needs.

        A renamed column is the one failure mode that looks like success: the
        import runs, reports a tidy number, and has quietly stopped recognising
        who lives here. Fix it by adding the new spelling to `HEADER_ALIASES`.
        """
        found = {
            ALIAS_TO_FIELD[column]
            for cell in header
            if (column := normalise_header(cell)) in ALIAS_TO_FIELD
        }
        missing = [name for name in REQUIRED_FIELDS if name not in found]
        if missing:
            raise CommandError(
                "Ingen kolonne genkendt for: "
                + ", ".join(missing)
                + ". Er det det rigtige udtræk? Ellers tilføj den nye stavemåde til "
                "HEADER_ALIASES i apps/accounts/register.py."
            )
        unknown = [
            str(cell)
            for cell in header
            if str(cell).strip() and normalise_header(cell) not in ALIAS_TO_FIELD
        ]
        if unknown:
            self.stdout.write(
                f"Kolonner uden betydning for portalen, ignoreret: {', '.join(unknown)}."
            )
