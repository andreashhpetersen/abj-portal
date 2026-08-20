"""
Read the shop-rental form's responses sheet and reconcile it with the database.

Run it on a timer — every few minutes is plenty for applications that take weeks
to become contracts. It is safe to run at any moment and any number of times:
each run reads the whole sheet, so a missed run, a deploy in the middle of one,
or a week of downtime all sort themselves out on the next pass.

    python manage.py sync_applications
    python manage.py sync_applications --dry-run   # parse and report, write nothing
"""

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from apps.shoprentals import sheets
from apps.shoprentals.ingest import (
    ALIAS_TO_FIELD,
    normalise_header,
    parse_rows,
    upsert_responses,
)


class Command(BaseCommand):
    help = "Synkroniser erhvervsansøgninger fra Google Forms' regneark."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Read and parse the sheet, report what would happen, write nothing.",
        )
        parser.add_argument(
            "--sheet-id",
            default=None,
            help="Override SHOPRENTALS_SHEET_ID, e.g. to try a copy of the sheet.",
        )
        parser.add_argument(
            "--range",
            dest="sheet_range",
            default=None,
            help="Override SHOPRENTALS_SHEET_RANGE. Defaults to the first sheet.",
        )

    def handle(self, *args, **options):
        try:
            header, rows = sheets.fetch_rows(
                spreadsheet_id=options["sheet_id"], sheet_range=options["sheet_range"]
            )
        except ImproperlyConfigured as error:
            # A configuration problem is the operator's to fix, and a traceback
            # in the timer's log helps nobody read it.
            raise CommandError(str(error)) from error

        if not header:
            self.stdout.write(self.style.WARNING("Regnearket er tomt — intet at gøre."))
            return

        responses, skipped = parse_rows(header, rows)

        if options["dry_run"]:
            self.stdout.write(
                f"Tørkørsel: {len(responses)} svar læst, {len(skipped)} rækker sprunget over."
            )
            self._report_skipped(skipped)
            self._report_unmapped(header)
            return

        result = upsert_responses(responses, skipped)
        self.stdout.write(
            self.style.SUCCESS(
                f"{result.created} nye, {result.updated} opdaterede, "
                f"{result.unchanged} uændrede ({result.total} i alt)."
            )
        )
        self._report_skipped(result.skipped_rows)
        self._report_unmapped(header)

    def _report_skipped(self, skipped):
        """Say so loudly. A silently dropped application is the worst outcome."""
        if not skipped:
            return
        rows = ", ".join(str(row) for row in skipped)
        self.stderr.write(
            self.style.WARNING(
                f"Sprang række(r) {rows} over: intet brugbart tidsstempel, "
                "så ansøgningen kan ikke få en stabil identitet."
            )
        )

    def _report_unmapped(self, header):
        """Warn when the form no longer supplies name, email or phone.

        Every answer is stored regardless, so nothing is lost — but the list view
        sorts and searches on these three, and a reworded question quietly
        emptying the applicant's name is worth a line in the log rather than a
        puzzled committee. Fix it by adding the new wording to HEADER_ALIASES.
        """
        found = {
            ALIAS_TO_FIELD[column]
            for cell in header
            if (column := normalise_header(cell)) in ALIAS_TO_FIELD
        }
        missing = {"applicant_name", "email", "phone"} - found
        if missing:
            self.stderr.write(
                self.style.WARNING(
                    "Ingen kolonne genkendt for: "
                    + ", ".join(sorted(missing))
                    + ". Svarene er gemt, men felterne står tomme — "
                    "tilføj formuleringen til HEADER_ALIASES i ingest.py."
                )
            )
