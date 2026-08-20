"""
Turning rows of a Google Forms response sheet into `Application` rows.

Deliberately free of any Google dependency: this module takes a header row and
a list of data rows — wherever they came from — and reconciles them with the
database. `sheets.py` is the only thing that talks to Google, which keeps the
interesting logic testable without credentials or a network.

Two properties matter more than anything else here.

**Reconciliation, not replay.** Every run reads the whole sheet and upserts.
A run that fails, a deploy that interrupts one, a fortnight of downtime, a cell
someone corrected by hand in the sheet — all of it resolves on the next pass,
because the sheet is the truth about what the applicant said and this code
simply makes the database agree. That is the reason for polling over a webhook.

**The committee's work is untouchable.** Upserting only ever writes the fields
the applicant filled in (through `Application.apply_form_data`). Status, rating,
assignee, comments and contract details are never written here. A sync that
reset a rating would be a sync nobody could safely run.

Rows are never deleted. If a row disappears from the sheet, the application it
created stays — by then it may carry months of committee work, and the sheet is
not the authority on that.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Application

#: Header aliases for the three fields lifted out of the answers into columns,
#: because the committee filters, sorts and searches on them.
#:
#: THIS IS THE ONE PLACE A FORM CHANGE HAS TO BE REFLECTED. Rewording a question
#: costs an entry here; asking a brand-new question costs nothing at all, since
#: anything unrecognised is still captured in `answers` and still displayed. Keep
#: the alternatives — the form has been through more than one wording, and old
#: exports should keep importing.
HEADER_ALIASES = {
    "applicant_name": (
        "navn",
        "dit navn",
        "fulde navn",
        "navn på ansøger",
        "ansøger",
        "kontaktperson",
        "name",
    ),
    "email": (
        "email",
        "e-mail",
        "emailadresse",
        "e-mailadresse",
        "mail",
        "mailadresse",
        "email address",
    ),
    "phone": (
        "telefon",
        "telefonnummer",
        "tlf",
        "tlf.",
        "mobil",
        "mobilnummer",
        "phone",
    ),
}

#: The form's own timestamp column. Google names it by the form's locale, hence
#: both spellings.
TIMESTAMP_HEADERS = ("tidsstempel", "timestamp")


def normalise_header(header):
    """Fold a column heading to something matchable.

    Headings arrive with the question's punctuation and casing attached, and
    Google appends a disambiguating suffix when two questions share a wording.
    Lowercase, strip the trailing punctuation, collapse whitespace.
    """
    folded = str(header or "").strip().lower()
    folded = re.sub(r"\s+", " ", folded)
    return folded.rstrip(" :?*.")


def _alias_lookup():
    """Reverse `HEADER_ALIASES` into {normalised heading: field name}."""
    return {
        normalise_header(alias): target
        for target, aliases in HEADER_ALIASES.items()
        for alias in aliases
    }


ALIAS_TO_FIELD = _alias_lookup()


@dataclass
class FormResponse:
    """One submission, mapped but not yet saved."""

    source_key: str
    submitted_at: datetime
    applicant_name: str = ""
    email: str = ""
    phone: str = ""
    answers: list = field(default_factory=list)
    source_row: int | None = None


def source_key(submitted_at, email, answers):
    """A stable identity for a submission.

    A Forms response sheet exposes no id of its own, and the row number is not
    usable: sorting or deleting a row above would silently re-point every key
    below it and the committee's notes would end up attached to the wrong
    applicant. So the key is a digest of the submission's own content — its
    timestamp and the applicant's email, which together identify a submission,
    falling back to the full set of answers when the form asks for neither.

    The consequence worth knowing: correcting an applicant's email *in the
    sheet* changes the key, so the next sync files it as a new application
    rather than an edit. Correct such things in the portal instead — which is
    where the committee works anyway.
    """
    if email:
        material = f"{submitted_at.isoformat()}|{email.strip().lower()}"
    else:
        joined = "|".join(f"{item['question']}={item['value']}" for item in answers)
        material = f"{submitted_at.isoformat()}|{joined}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def parse_timestamp(raw):
    """Read the form's timestamp column into an aware datetime.

    Sheets hands it over as a locale-formatted string. Try ISO first — that is
    what the API returns when the sheet is read unformatted — then Danish
    day-first formats. Naive results are localised to the association's
    timezone, which is the one the form's respondents and the sheet both use.
    """
    text = str(raw or "").strip()
    if not text:
        return None

    parsed = parse_datetime(text)
    if parsed is None:
        for pattern in (
            "%d/%m/%Y %H.%M.%S",
            "%d/%m/%Y %H:%M:%S",
            "%d-%m-%Y %H.%M.%S",
            "%d-%m-%Y %H:%M:%S",
            "%d/%m/%Y %H.%M",
            "%d/%m/%Y %H:%M",
        ):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue

    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def parse_rows(header, rows, *, first_data_row=2):
    """Map a sheet into `FormResponse` objects, skipping what cannot be used.

    `first_data_row` is the spreadsheet row number of `rows[0]` — 2 for a normal
    responses sheet, where row 1 is the header. It is recorded on the
    application for fault-finding only.

    A row with no usable timestamp is skipped: without one there is no stable
    identity, and inventing one would mean the row arrives afresh on every sync.
    Blank rows — the trailing emptiness every sheet has — are skipped silently.
    """
    columns = [normalise_header(cell) for cell in header]
    responses = []
    skipped = []

    for offset, row in enumerate(rows):
        row_number = first_data_row + offset
        cells = [str(cell).strip() if cell is not None else "" for cell in row]
        if not any(cells):
            continue

        # Short rows are normal: Sheets truncates trailing empty cells.
        cells += [""] * (len(columns) - len(cells))

        mapped = {"applicant_name": "", "email": "", "phone": ""}
        answers = []
        submitted_at = None

        for index, column in enumerate(columns):
            value = cells[index]
            if column in TIMESTAMP_HEADERS:
                submitted_at = parse_timestamp(value)
                continue
            if not column:
                continue
            target = ALIAS_TO_FIELD.get(column)
            if target and not mapped[target]:
                mapped[target] = value
            # Recognised or not, every answer is kept — verbatim, in the form's
            # own order and wording. This is what makes a form change harmless.
            # Indexed by position rather than by heading, so two questions that
            # happen to share a wording stay two separate answers.
            answers.append({"question": str(header[index]).strip(), "value": value})

        if submitted_at is None:
            skipped.append(row_number)
            continue

        responses.append(
            FormResponse(
                source_key=source_key(submitted_at, mapped["email"], answers),
                submitted_at=submitted_at,
                applicant_name=mapped["applicant_name"][:200],
                email=mapped["email"][:254],
                phone=mapped["phone"][:64],
                answers=answers,
                source_row=row_number,
            )
        )

    return responses, skipped


@dataclass
class SyncResult:
    """What a sync run did, for the command to report and for tests to assert."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_rows: list = field(default_factory=list)

    @property
    def total(self):
        return self.created + self.updated + self.unchanged


@transaction.atomic
def upsert_responses(responses, skipped_rows=None):
    """Reconcile parsed responses with the database.

    Atomic: a sheet half-read is worse than one not read, and the next run will
    pick it all up regardless.
    """
    result = SyncResult(skipped_rows=list(skipped_rows or []))
    existing = {
        application.source_key: application
        for application in Application.objects.filter(
            source_key__in=[response.source_key for response in responses]
        )
    }

    for response in responses:
        application = existing.get(response.source_key)
        if application is None:
            Application.objects.create(
                source_key=response.source_key,
                submitted_at=response.submitted_at,
                applicant_name=response.applicant_name,
                email=response.email,
                phone=response.phone,
                answers=response.answers,
                source_row=response.source_row,
                synced_at=timezone.now(),
            )
            result.created += 1
            continue

        # Only the applicant's own fields. Status, rating, assignee, comments
        # and contract details belong to the committee and are not ours to move.
        changed = application.apply_form_data(
            submitted_at=response.submitted_at,
            applicant_name=response.applicant_name,
            email=response.email,
            phone=response.phone,
            answers=response.answers,
            source_row=response.source_row,
        )
        if changed:
            result.updated += 1
        else:
            result.unchanged += 1

    return result


def sync_sheet(header, rows, *, first_data_row=2):
    """Parse and upsert in one call — what the management command runs."""
    responses, skipped = parse_rows(header, rows, first_data_row=first_data_row)
    return upsert_responses(responses, skipped)
