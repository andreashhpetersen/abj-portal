"""
The only place the portal talks to Google.

Kept to one thin function so that everything interesting about ingestion —
mapping headings, deriving identities, deciding what a sync may overwrite —
lives in `ingest.py` and is testable without credentials or a network. If the
association ever moves off Google Forms, this is the file that gets replaced.

Access is a **read-only service account**. Create one, download its JSON key,
and share the responses spreadsheet with the service account's email address
exactly as you would share it with a colleague; nothing else grants it access,
and it can reach no other file in the Drive. See OPERATIONS.md.

The Google client libraries are imported inside the function on purpose: the
rest of the portal must import and run without them installed, so that a
checkout with no Google setup still passes `manage.py check` and the test suite.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

#: Read-only. The portal has no business writing to the association's form.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def fetch_rows(spreadsheet_id=None, sheet_range=None, credentials_file=None):
    """Return (header, rows) from the responses sheet.

    Values come back as the sheet displays them — `FORMATTED_VALUE` — so the
    form's timestamp arrives as the Danish string a human would read rather than
    as a serial number that needs the spreadsheet epoch to decode. Everything is
    stringified downstream anyway, so there is nothing to gain from the
    alternative and a date-shaped bug to lose.
    """
    spreadsheet_id = spreadsheet_id or settings.SHOPRENTALS_SHEET_ID
    sheet_range = sheet_range or settings.SHOPRENTALS_SHEET_RANGE
    credentials_file = credentials_file or settings.SHOPRENTALS_GOOGLE_CREDENTIALS

    if not spreadsheet_id:
        raise ImproperlyConfigured(
            "SHOPRENTALS_SHEET_ID is not set — there is no responses sheet to read."
        )
    if not credentials_file:
        raise ImproperlyConfigured(
            "SHOPRENTALS_GOOGLE_CREDENTIALS is not set — no service-account key to read it with."
        )

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise ImproperlyConfigured(
            "The Google client libraries are not installed. Run `pip install -r requirements.txt`."
        ) from error

    # The likeliest failures in production are a path that is right on the host
    # but wrong inside the container, and a key the container's uid cannot read.
    # Both deserve a sentence in the timer's log rather than a traceback.
    try:
        credentials = service_account.Credentials.from_service_account_file(
            credentials_file, scopes=SCOPES
        )
    except OSError as error:
        raise ImproperlyConfigured(
            f"Cannot read the service-account key at {credentials_file}: {error}. "
            "In the container this must be the mounted path, and the file must be "
            "readable by uid 10001 — see OPERATIONS.md."
        ) from error
    except ValueError as error:
        raise ImproperlyConfigured(
            f"{credentials_file} is not a usable service-account key: {error}"
        ) from error
    # cache_discovery=False: the default file cache warns noisily under a
    # non-writable working directory, which is exactly how the timer runs it.
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    response = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=sheet_range,
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )

    values = response.get("values", [])
    if not values:
        return [], []
    return values[0], values[1:]
