"""
The only code that knows the resident register arrives as a CSV file.

`register.py` deals in a header and a list of rows and does not care where they
came from; this turns a download into those two things. It is the counterpart of
`shoprentals/sheets.py`, and exists for the same reason: when INNA finally
offers an API, everything that has to change is here.

**The dialect is detected rather than asked for.** A board member exports the
register, and there is a fair chance the file reaches the portal having been
opened and re-saved in Excel — which on a Danish machine writes cp1252 with
semicolons, not the UTF-8 with commas INNA produces. Asking them to know that is
asking the wrong person. Instead every plausible combination is tried and the
one that makes the required columns appear is the one that read the file
correctly: a wrong separator leaves the whole line in a single cell and a wrong
encoding mangles `Enhedstype` into something the alias table does not match, so
`register.missing_required_fields` is a reliable test of having got it right.

Nothing here writes to disk. The admin upload hands over bytes that came
straight from the request and are dropped when it ends; the management command
reads a path because somebody with a shell already has the file.
"""

import csv
import io
from pathlib import Path

from django.utils.translation import gettext_lazy as _

from .register import missing_required_fields

#: Tried in order. UTF-8 with commas is what INNA exports; cp1252 with
#: semicolons is what Excel produces from it on a Danish machine. `utf-8-sig`
#: rather than `utf-8` because a byte-order mark would otherwise ride along on
#: the first heading and stop `Fornavn` matching.
ENCODINGS = ("utf-8-sig", "cp1252")
DELIMITERS = (",", ";", "\t")

#: Refused outright. The real register is about 130 kB, and the limit sits well
#: under Django's `FILE_UPLOAD_MAX_MEMORY_SIZE` of 2.5 MB so that an upload is
#: held in memory and never spills into a temporary file on the server — see the
#: module docstring. A file this far out of range is not the register.
MAX_BYTES = 2 * 1024 * 1024


class RegisterFileError(Exception):
    """The file is not a readable register export.

    Carries a message meant for whoever uploaded it, so both the admin form and
    the management command can show it unchanged.
    """


def read_bytes(data):
    """Read a downloaded export into `(header, rows)`.

    Raises `RegisterFileError`, with a Danish explanation, for anything that is
    not a register export — the wrong file, an empty one, or one whose columns
    INNA has renamed.
    """
    if not data:
        raise RegisterFileError(_("Filen er tom."))
    if len(data) > MAX_BYTES:
        raise RegisterFileError(
            _(
                "Filen fylder %(size)d MB. Beboerudtrækket fylder omkring 0,1 MB, så det er "
                "næppe den rigtige fil."
            )
            % {"size": round(len(data) / 1024 / 1024)}
        )

    attempted = False
    for encoding in ENCODINGS:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        for delimiter in DELIMITERS:
            rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
            if not rows:
                continue
            attempted = True
            if not missing_required_fields(rows[0]):
                return rows[0], rows[1:]

    raise RegisterFileError(_read_failure(attempted))


def read_path(path):
    """The same, for a file on disk — what the management command uses."""
    path = Path(path)
    if not path.exists():
        raise RegisterFileError(_("Filen findes ikke: %(path)s") % {"path": path})
    return read_bytes(path.read_bytes())


def _read_failure(attempted):
    """Say which of the two failures happened, because the fixes differ.

    A file that could not be read as a table at all is the wrong file. One that
    parsed but lacks the required columns is very likely the right file with a
    renamed column, which is a change to `HEADER_ALIASES` and not something the
    person uploading can do anything about — so the message has to say so
    rather than leave them re-exporting.
    """
    if not attempted:
        return _("Filen kunne ikke læses som en CSV-fil. Hentede du den som CSV hos INNA?")
    return _(
        "Filen kunne læses, men den mangler de kolonner, portalen skal bruge "
        "(Bolignr., Adresse, Beboernr., Enhedstype og Role hos CS). Enten er det "
        "en anden fil — eller INNA har omdøbt en kolonne, og så skal den nye "
        "stavemåde tilføjes i HEADER_ALIASES i apps/accounts/register.py."
    )
