"""
The sync command.

Google is stubbed out at `sheets.fetch_rows` — the seam the whole module exists
to provide. What is worth testing here is the reporting: an operator reading the
timer's log has to be able to tell "nothing new" from "six applications were
dropped on the floor".
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.shoprentals import sheets
from apps.shoprentals.models import Application

HEADER = ["Tidsstempel", "Navn", "Email", "Telefonnummer", "Formål"]
ROW = ["20/08/2026 14.32.05", "Mette Sørensen", "mette@blomster.dk", "12345678", "Blomsterbutik"]


@pytest.fixture
def sheet(monkeypatch):
    """Stub the Google call. Assign `sheet.rows` to change what it returns."""

    class Stub:
        header = HEADER
        rows = [ROW]

    stub = Stub()
    monkeypatch.setattr(sheets, "fetch_rows", lambda **_kwargs: (stub.header, stub.rows))
    return stub


def test_it_reports_what_it_created(db, sheet, capsys):
    call_command("sync_applications")
    assert "1 nye, 0 opdaterede, 0 uændrede" in capsys.readouterr().out
    assert Application.objects.count() == 1


def test_running_it_twice_reports_nothing_changed(db, sheet, capsys):
    call_command("sync_applications")
    capsys.readouterr()
    call_command("sync_applications")
    assert "0 nye, 0 opdaterede, 1 uændrede" in capsys.readouterr().out


def test_a_dry_run_writes_nothing(db, sheet, capsys):
    call_command("sync_applications", "--dry-run")
    assert "Tørkørsel: 1 svar læst" in capsys.readouterr().out
    assert Application.objects.count() == 0


def test_a_skipped_row_is_named_on_stderr(db, sheet, capsys):
    """The operator needs the row number, not a count."""
    sheet.rows = [ROW, ["ukendt", "Kasper", "kasper@dk", "", ""]]
    call_command("sync_applications")
    assert "Sprang række(r) 3 over" in capsys.readouterr().err


def test_a_reworded_contact_question_is_warned_about(db, sheet, capsys):
    """Nothing is lost — but the name column silently emptying is worth a line
    in the log rather than a puzzled committee."""
    sheet.header = ["Tidsstempel", "Hvem er du?", "Email", "Telefonnummer", "Formål"]
    call_command("sync_applications")
    err = capsys.readouterr().err
    assert "applicant_name" in err
    assert "HEADER_ALIASES" in err


def test_a_form_the_aliases_fully_recognise_produces_no_warning(db, sheet, capsys):
    call_command("sync_applications")
    assert "Ingen kolonne genkendt" not in capsys.readouterr().err


def test_an_empty_sheet_is_not_an_error(db, sheet, capsys):
    sheet.header = []
    sheet.rows = []
    call_command("sync_applications")
    assert "tomt" in capsys.readouterr().out


def test_a_missing_configuration_is_a_clean_command_error(db, monkeypatch):
    """A traceback in the timer's log helps nobody read it."""

    def unconfigured(**_kwargs):
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured("SHOPRENTALS_SHEET_ID is not set")

    monkeypatch.setattr(sheets, "fetch_rows", unconfigured)
    with pytest.raises(CommandError, match="SHOPRENTALS_SHEET_ID"):
        call_command("sync_applications")
