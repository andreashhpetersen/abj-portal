"""
Read a resident-register export from INNA and reconcile it with the database.

**The normal way to do this is the admin**, at *Beboerregister → Importér
beboerregister*: the board members who fetch the export from INNA are not the
people with a shell on the server, and an upload leaves no copy of six hundred
residents' details lying about afterwards. This command is the same import for
anyone who does have a shell — a developer working locally, or an operator with
the file already on the machine.

    python manage.py import_residents data/register/users-2026-09-08.csv --dry-run
    python manage.py import_residents data/register/users-2026-09-08.csv

Safe to run at any moment and any number of times. Each run reads the whole
export, so an interrupted run, a month of nobody bothering, or a correction INNA
made last week all sort themselves out on the next pass. Nothing is ever
deleted: somebody who has stopped appearing is flagged, and the board decides.

Everything this prints comes from `register.py`, which is also what the admin
page renders, so both routes say the same thing about the same file.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.accounts import csvsource, register


class Command(BaseCommand):
    help = "Importér beboerregistret fra en CSV-fil hentet hos INNA."

    #: Warnings go to stderr so a timer or a CI step can tell "it worked" from
    #: "read this". Everything else is ordinary output.
    STREAMS = {register.WARNING: "stderr"}
    STYLES = {
        register.SUCCESS: "SUCCESS",
        register.WARNING: "WARNING",
        register.INFO: "HTTP_INFO",
    }

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

    def handle(self, *args, **options):
        try:
            header, rows = csvsource.read_path(options["path"])
        except csvsource.RegisterFileError as error:
            # The operator's to fix, and a traceback helps nobody read a message
            # that already says what is wrong with their file.
            raise CommandError(str(error)) from error

        self._write(register.header_notes(header))

        if options["dry_run"]:
            parsed, skipped = register.parse_rows(header, rows)
            self._write(register.dry_run_notes(parsed, skipped))
            return

        self._write(register.outcome_notes(register.run_import(header, rows)))

    def _write(self, notes):
        for note in notes:
            stream = getattr(self, self.STREAMS.get(note.level, "stdout"))
            stream.write(getattr(self.style, self.STYLES[note.level])(str(note.text)))
