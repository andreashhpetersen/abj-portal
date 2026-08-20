"""
Reading the responses sheet.

No Google here: `ingest` takes a header and rows from anywhere, which is the
reason it was split out of `sheets`. These tests feed it the shapes a real
Forms sheet produces — Danish timestamps, reworded questions, short rows,
trailing blanks — and pin down the two properties the design rests on: syncing
is idempotent, and it never touches the committee's own work.
"""

from datetime import datetime

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.shoprentals.ingest import (
    normalise_header,
    parse_rows,
    parse_timestamp,
    source_key,
    sync_sheet,
)
from apps.shoprentals.models import Application, ApplicationComment, ApplicationStatus

User = get_user_model()

HEADER = [
    "Tidsstempel",
    "Navn",
    "Email",
    "Telefonnummer",
    "Hvad vil du bruge lejemålet til?",
]


def row(
    when="20/08/2026 14.32.05",
    name="Mette Sørensen",
    email="mette@blomster.dk",
    phone="12345678",
    purpose="Blomsterbutik",
):
    return [when, name, email, phone, purpose]


def test_a_heading_is_matched_regardless_of_case_punctuation_and_spacing():
    assert normalise_header("  E-Mailadresse:  ") == "e-mailadresse"
    assert normalise_header("Hvad vil du bruge lejemålet til?") == "hvad vil du bruge lejemålet til"
    assert normalise_header("Navn *") == "navn"
    assert normalise_header(None) == ""


def test_only_a_questions_first_line_identifies_it():
    """A Google Form question carries its description below the title, and the
    responses sheet flattens both into one heading. The real form has exactly
    this shape, so matching the whole thing would recognise nothing."""
    assert normalise_header("Navn\n(Fornavn(e) + Efternavn)") == "navn"
    assert (
        normalise_header("Konceptbeskrivelse\nLav en grundig beskrivelse af dit koncept.")
        == "konceptbeskrivelse"
    )


def test_a_contact_question_with_a_description_still_fills_its_column():
    """The bug this caught: the committee's list showed no applicant names,
    because every heading in the real form has a second line."""
    header = ["Tidsstempel", "Navn\n(Fornavn(e) + Efternavn)", "Mailadresse", "Telefonnummer"]
    responses, _skipped = parse_rows(
        header, [["20/08/2026 14.32.05", "Mette Sørensen", "mette@blomster.dk", "12345678"]]
    )
    (response,) = responses
    assert response.applicant_name == "Mette Sørensen"
    assert response.email == "mette@blomster.dk"
    assert response.phone == "12345678"
    # And the full heading, description included, is what gets displayed.
    assert response.answers[0]["question"] == "Navn\n(Fornavn(e) + Efternavn)"


def test_two_questions_sharing_a_title_lift_only_the_first():
    """They fold to the same key. Both answers survive; only one column is set."""
    header = ["Tidsstempel", "Navn\n(på dig)", "Navn\n(på din revisor)"]
    responses, _skipped = parse_rows(header, [["20/08/2026 14.32.05", "Mette", "Jens"]])
    (response,) = responses
    assert response.applicant_name == "Mette"
    assert [item["value"] for item in response.answers] == ["Mette", "Jens"]


def test_the_danish_timestamp_format_google_writes_is_understood():
    parsed = parse_timestamp("20/08/2026 14.32.05")
    assert parsed is not None
    local = timezone.localtime(parsed)
    assert (local.year, local.month, local.day) == (2026, 8, 20)
    assert (local.hour, local.minute) == (14, 32)


def test_an_iso_timestamp_is_understood_too():
    assert parse_timestamp("2026-08-20T14:32:05") is not None


def test_a_naive_timestamp_is_read_as_local_time_not_utc():
    """The form's respondents and the sheet are both in Copenhagen. Reading the
    timestamp as UTC would file an evening submission on the previous day."""
    parsed = parse_timestamp("20/08/2026 00.30.00")
    assert timezone.localtime(parsed).day == 20


def test_an_unreadable_timestamp_is_reported_rather_than_guessed():
    assert parse_timestamp("i går") is None
    assert parse_timestamp("") is None


def test_every_answer_is_kept_verbatim_in_the_forms_own_order():
    responses, _skipped = parse_rows(HEADER, [row()])
    (response,) = responses
    assert [item["question"] for item in response.answers] == [
        "Navn",
        "Email",
        "Telefonnummer",
        "Hvad vil du bruge lejemålet til?",
    ]
    assert response.answers[-1]["value"] == "Blomsterbutik"


def test_the_three_searchable_fields_are_lifted_out_of_the_answers():
    responses, _skipped = parse_rows(HEADER, [row()])
    (response,) = responses
    assert response.applicant_name == "Mette Sørensen"
    assert response.email == "mette@blomster.dk"
    assert response.phone == "12345678"


def test_a_question_the_aliases_do_not_know_still_arrives():
    """The point of the design: the form can grow without a code change."""
    header = [*HEADER, "Har du drevet butik før?"]
    responses, _skipped = parse_rows(header, [[*row(), "Ja, i ti år"]])
    (response,) = responses
    assert {"question": "Har du drevet butik før?", "value": "Ja, i ti år"} in response.answers


def test_a_reworded_contact_question_leaves_the_column_empty_but_loses_nothing():
    """If the form renames "Email" to something unrecognised, the answer is
    still stored — only the lifted-out field goes blank, and the sync command
    warns about exactly that."""
    header = ["Tidsstempel", "Navn", "Din elektroniske postadresse", "Telefonnummer", "Formål"]
    responses, _skipped = parse_rows(header, [row()])
    (response,) = responses
    assert response.email == ""
    assert any(item["value"] == "mette@blomster.dk" for item in response.answers)


def test_two_questions_with_the_same_wording_stay_two_answers():
    header = ["Tidsstempel", "Andet", "Andet"]
    responses, _skipped = parse_rows(header, [["20/08/2026 14.32.05", "første", "andet"]])
    (response,) = responses
    assert [item["value"] for item in response.answers] == ["første", "andet"]


def test_a_short_row_is_padded_rather_than_dropped():
    """Sheets truncates trailing empty cells, so most real rows are short."""
    responses, _skipped = parse_rows(HEADER, [["20/08/2026 14.32.05", "Mette"]])
    (response,) = responses
    assert response.applicant_name == "Mette"
    assert response.email == ""


def test_blank_rows_are_skipped_silently():
    responses, skipped = parse_rows(HEADER, [row(), ["", "", "", "", ""], []])
    assert len(responses) == 1
    assert skipped == []


def test_a_row_without_a_timestamp_is_skipped_and_named():
    """Named, not silent: a dropped application is the worst possible outcome,
    so the command prints the row number to go and look at."""
    responses, skipped = parse_rows(HEADER, [row(), row(when="ukendt")])
    assert len(responses) == 1
    assert skipped == [3]  # row 1 is the header, so rows[1] is sheet row 3


def test_the_identity_of_a_submission_is_its_timestamp_and_email():
    when = timezone.make_aware(datetime(2026, 8, 20, 14, 32))
    assert source_key(when, "mette@blomster.dk", []) == source_key(when, "Mette@Blomster.DK ", [])
    assert source_key(when, "mette@blomster.dk", []) != source_key(when, "anden@dk", [])


def test_a_form_asking_for_no_email_still_yields_stable_identities():
    when = timezone.make_aware(datetime(2026, 8, 20, 14, 32))
    answers = [{"question": "Formål", "value": "Blomsterbutik"}]
    assert source_key(when, "", answers) == source_key(when, "", answers)
    assert source_key(when, "", answers) != source_key(
        when, "", [{"question": "Formål", "value": "Bodega"}]
    )


# --- reconciliation -------------------------------------------------------


@pytest.fixture
def committee_member(db):
    return User.objects.create_user(email="udvalg@ab-jaeger.dk", password="hemmeligt123")


def test_a_first_sync_creates_the_applications(db):
    result = sync_sheet(HEADER, [row(), row(email="anden@dk", when="21/08/2026 09.00.00")])
    assert (result.created, result.updated, result.unchanged) == (2, 0, 0)
    assert Application.objects.count() == 2


def test_syncing_the_same_sheet_again_changes_nothing(db):
    sync_sheet(HEADER, [row()])
    result = sync_sheet(HEADER, [row()])
    assert (result.created, result.updated, result.unchanged) == (0, 0, 1)
    assert Application.objects.count() == 1


def test_a_corrected_answer_in_the_sheet_reaches_the_application(db):
    """Reconciliation, not replay: the sheet is the truth about what was said."""
    sync_sheet(HEADER, [row(purpose="Blomsterbutik")])
    result = sync_sheet(HEADER, [row(purpose="Blomster- og gavebutik")])
    assert result.updated == 1
    application = Application.objects.get()
    assert application.answers[-1]["value"] == "Blomster- og gavebutik"


def test_a_resync_does_not_touch_the_committees_own_work(db, committee_member):
    """The invariant the whole design rests on. If this ever fails, the sync is
    not safe to run on a timer and the committee loses work silently."""
    sync_sheet(HEADER, [row()])
    application = Application.objects.get()
    application.set_status(ApplicationStatus.SAVED)
    application.rating = 4
    application.assignee = committee_member
    application.save()
    ApplicationComment.objects.create(
        application=application, author=committee_member, body="Ringet 20/8, vender tilbage."
    )
    changed_at = Application.objects.get().status_changed_at

    # Including a run where the applicant's own answers did change.
    sync_sheet(HEADER, [row(purpose="Blomster- og gavebutik")])

    application.refresh_from_db()
    assert application.status == ApplicationStatus.SAVED
    assert application.status_changed_at == changed_at
    assert application.rating == 4
    assert application.assignee == committee_member
    assert application.comments.count() == 1


def test_a_row_vanishing_from_the_sheet_leaves_its_application_alone(db):
    """By the time a row is deleted upstream, the application may carry months
    of notes. The sheet is not the authority on those."""
    sync_sheet(HEADER, [row(), row(email="anden@dk", when="21/08/2026 09.00.00")])
    sync_sheet(HEADER, [row()])
    assert Application.objects.count() == 2


def test_new_applications_start_as_new_and_unrated(db):
    sync_sheet(HEADER, [row()])
    application = Application.objects.get()
    assert application.status == ApplicationStatus.NEW
    assert application.rating is None
    assert application.status_changed_at is None
    assert application.synced_at is not None


def test_skipped_rows_are_carried_through_to_the_result(db):
    result = sync_sheet(HEADER, [row(), row(when="ukendt")])
    assert result.skipped_rows == [3]
