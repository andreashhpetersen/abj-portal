"""
Reading INNA's resident register into rows the portal can check a claim against.

INNA — the administration company, Cobblestone under its old name, which is
where the ``Role hos CS`` column gets its initials — is the source of truth for
who lives in the association. It has no API yet, so a board member downloads a
CSV export from its web interface and feeds it to ``manage.py import_residents``.

Deliberately free of any file handling: this module takes a header row and a
list of data rows, wherever they came from, and reconciles them with the
database. The command owns the CSV, this owns the meaning — the same seam as
``shoprentals.ingest``, and for the same reason: it is what makes the
interesting logic testable without a real dump, and a real dump is 600 people's
names, emails and home addresses.

Three properties matter here.

**Reconciliation, not replay.** Every run reads the whole export and upserts.
A half-finished run, a dump taken during a move, a fortnight of not bothering —
all of it resolves on the next pass, because the export is the truth about who
lives here and this code simply makes the database agree.

**A register entry is not a user.** Most people in the export have never used
the portal and some have no email address in it at all, so importing does not
create accounts. It creates the list that a signup is checked against; the
account still comes from somebody signing up. See ``RegisterEntry``.

**Nothing is deleted.** A person who disappears from the export is marked
``is_current=False``, not removed: by then their account may own bookings, and
a row that stopped appearing is a question for the board rather than an answer.

What the export actually looks like, which is why the code below is shaped as
it is:

* ``Bolignr.`` is the **unit** — a flat, a shop, a storage room. It is stable,
  it is the only field that is never blank or malformed, and it is what this
  module uses as identity.
* ``Beboernr.`` is the **tenancy**, and two people sharing a flat share one. It
  is therefore not unique and cannot identify a person. It gains a new final
  segment when a flat changes hands, so the same unit can briefly carry two —
  the export contains move-ins that have not happened yet.
* ``Enhedstype`` is blank for household members, and ``Role hos CS`` is what
  distinguishes a spouse from a caretaker. Eligibility needs both columns.
* Addresses are free text with real-world noise in them: doubled spaces, a
  missing full stop, ``4. t.v``, a storage room described as ``Rum I``.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import (
    Building,
    RegisterEntry,
    Resident,
    SignupRequest,
    SignupRequestStatus,
    User,
    resident_number_validator,
    unit_number_validator,
)

#: Column headings, normalised, mapped to the fields this module understands.
#:
#: THIS IS THE ONE PLACE A CHANGED EXPORT HAS TO BE REFLECTED. INNA owns the
#: column names and has already renamed itself once; keep old spellings when
#: adding new ones, so a dump taken last year still imports. A column that is
#: not listed here is ignored, and the command says which ones it did not
#: recognise.
HEADER_ALIASES = {
    "first_name": ("fornavn",),
    "last_name": ("efternavn",),
    "alias": ("alias navn", "alias", "kaldenavn"),
    "unit_number": ("bolignr", "boligno", "boligsnr", "lejemålsnr"),
    "raw_address": ("adresse",),
    "city": ("by",),
    "postal_code": ("postnr", "postnummer"),
    "email": ("e-mail", "email", "mail", "e-mailadresse"),
    "phone": ("tlf nr", "tlf", "telefon", "telefonnummer", "mobil"),
    "role": ("role hos cs", "rolle hos cs", "rolle", "role"),
    "resident_number": ("beboernr", "beboernummer"),
    "unit_type": ("enhedstype", "enhed"),
    "moved_in": ("indflytningsdato", "indflytning"),
}

#: `Enhedstype` values that mean somebody lives there. "Bolig" is the same thing
#: as "Andelsbolig" typed by a different hand — three flats in the export carry
#: it — and excluding them would lock three residents out over a data-entry
#: variance. Everything else (Erhverv, Loft/Kælder/Depotrum) is a shop or a
#: storage room: real property in the association, but nobody's home.
RESIDENTIAL_UNIT_TYPES = ("andelsbolig", "bolig")

#: `Enhedstype` values the import knows are *not* somebody's home. Listed only
#: so that a value which is neither these nor residential can be reported as
#: unrecognised — see `unclassified_rows`. Eligibility never consults this: a
#: new kind of non-residential unit must fail by not being residential, not by
#: being on a list somebody remembered to extend.
NON_RESIDENTIAL_UNIT_TYPES = ("erhverv", "loft/kælder/depotrum")

#: `Role hos CS` for a spouse, partner or adult child living in someone else's
#: flat. They are the exception to needing a `Beboernr.`: the export gives them
#: none and leaves their `Enhedstype` blank, but they live here as much as the
#: andelshaver does. Their `Bolignr.` is real, so eligibility for them is
#: decided by whether that unit is residential — which is a question about the
#: whole export, not about their own row.
HOUSEHOLD_ROLE = "husstandsmedlem"

#: Borrowed from the model validators rather than restated, so an import cannot
#: accept a number that `Resident.full_clean()` would then refuse. A `Bolignr.`
#: of `11121` — which the caretakers have — is junk and is meant to fail this.
UNIT_NUMBER_RE = unit_number_validator.regex
RESIDENT_NUMBER_RE = resident_number_validator.regex

#: Floor labels as the export writes them, folded to how `Resident` stores them.
#: Anything else that is not a bare number — `nr.`, `Rum`, `nedre kld` — belongs
#: to a storage room and is meant to fail parsing.
FLOOR_ALIASES = {
    "st": "st.",
    "stuen": "st.",
    "kld": "kld.",
    "kl": "kld.",
    "kælder": "kld.",
}

#: Door labels. `t.v` and a missing full stop are both in the real export.
DOOR_ALIASES = {
    "th": "th",
    "t.h": "th",
    "tv": "tv",
    "t.v": "tv",
    "mf": "mf",
    "mdt": "mf",
}

#: Danish date formats the export has been seen to use.
DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d")


def normalise_header(header):
    """Fold a column heading to something matchable.

    Lowercase, and treat a full stop as whitespace so that `Tlf. Nr.`, `Tlf nr`
    and `Tlf.Nr` are one heading rather than three entries in the alias table.
    The export abbreviates half its columns and is not consistent about it —
    `Bolignr.` and `Postnr.` have the stop, `Enhedstype` does not — and getting
    this wrong is silent: an unrecognised column is simply not imported, and
    every row keeps the field blank.
    """
    folded = re.sub(r"[.\s]+", " ", str(header or "")).strip().lower()
    return folded.rstrip(" :?*")


def _alias_lookup():
    return {
        normalise_header(alias): target
        for target, aliases in HEADER_ALIASES.items()
        for alias in aliases
    }


ALIAS_TO_FIELD = _alias_lookup()

#: Columns without which an export cannot be used at all. Everything else is
#: optional — a missing `Tlf. Nr.` costs a phone number, a missing `Bolignr.`
#: costs the identity of every row in the file.
#:
#: Also what tells a register export from any other CSV, which is how
#: `csvsource` picks the encoding and separator: the combination that makes
#: these five appear is the one that read the file correctly.
REQUIRED_FIELDS = ("unit_number", "raw_address", "resident_number", "unit_type", "role")


def recognised_fields(header):
    """The fields this module can fill in from the given header row."""
    return {
        ALIAS_TO_FIELD[column]
        for cell in header
        if (column := normalise_header(cell)) in ALIAS_TO_FIELD
    }


def missing_required_fields(header):
    """Required fields the header does not supply, in a readable order.

    Empty means the file is a register export that can be imported. Non-empty
    is the one failure that otherwise looks like success — a renamed column
    imports cleanly and quietly stops recognising who lives here.
    """
    found = recognised_fields(header)
    return [name for name in REQUIRED_FIELDS if name not in found]


def unrecognised_headers(header):
    """Columns the portal has no use for. Reported, never an error.

    A new question in the export costs nothing; this exists so that a *renamed*
    one is visible next to the fields that went missing.
    """
    return [
        str(cell)
        for cell in header
        if str(cell).strip() and normalise_header(cell) not in ALIAS_TO_FIELD
    ]


def name_key(*parts):
    """A comparable form of a person's name, used as half of the row identity.

    The export has no id for a person — only for their unit — so identity here
    is the unit plus the name, which is what tells two people in one flat apart.
    Folding it means a correction to the capitalisation or the spacing of a name
    updates the existing row instead of creating a second one beside it.

    Accents are kept: `Søren` and `Soren` are different people as far as this is
    concerned, and merging them would be worse than duplicating them. What is
    dropped is case, repeated whitespace, and the lone `-` the export uses where
    a surname is unknown ("ANONYM -", the caretakers).
    """
    joined = " ".join(str(part or "") for part in parts)
    folded = unicodedata.normalize("NFC", joined)
    folded = re.sub(r"\s+", " ", folded).strip().lower()
    return folded.strip(" -,.").strip()


def normalise_number(raw):
    """Strip an identity number to its digits and dashes.

    Returns "" for anything that is blank or does not look like a number at
    all, so callers can treat "missing" and "unusable" the same way.
    """
    text = re.sub(r"\s+", "", str(raw or ""))
    return text if re.fullmatch(r"[\d-]+", text) else ""


def parse_date(raw):
    """Read `Indflytningsdato`, returning None rather than raising.

    Deliberately not used for eligibility: the export contains move-ins months
    in the future, and somebody who takes over a flat in December has every
    reason to book the beboerlokale for their housewarming in November.
    """
    text = str(raw or "").strip()
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


@dataclass
class ParsedAddress:
    """A free-text address broken into the pieces `Resident` stores."""

    street: str
    house_number: str
    floor: str
    door: str


def parse_address(raw):
    """Split `Jægersborggade 5, 1. tv.` into street, number, floor and door.

    Returns None when the text is not a flat. That is the intended outcome for
    a third of the storage rooms — `Jægersborggade 27 Kld. rum`, `Rum I`,
    `nedre kld nr. 409` — and it is also the last line of defence for
    eligibility: a row whose address cannot be read cannot be given to a
    `Resident`, so it is never eligible however its `Enhedstype` reads.

    Real noise handled on the way through: doubled spaces, a house number
    written `138 B`, a missing full stop after `tv`, `t.v`, `3. sal`, and a
    trailing remark such as `2. tv. Sammenlagt med LM 5507`.
    """
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if "," not in text:
        return None

    street_part, home_part = (piece.strip() for piece in text.split(",", 1))

    street_match = re.fullmatch(r"(.+?) (\d+ ?[A-Za-zÆØÅæøå]?)", street_part)
    if not street_match:
        return None
    street = street_match.group(1).strip()
    house_number = street_match.group(2).replace(" ", "").upper()

    tokens = home_part.split()
    if not tokens:
        return None

    floor = _parse_floor(tokens[0])
    if floor is None:
        return None
    # Only the token straight after the floor can be the door. Anything further
    # along — "sal", "Sammenlagt med LM 5507" — is a remark, and the raw address
    # is stored anyway for whoever needs to read it.
    door = DOOR_ALIASES.get(tokens[1].rstrip(".").lower(), "") if len(tokens) > 1 else ""
    return ParsedAddress(street=street, house_number=house_number, floor=floor, door=door)


def _parse_floor(token):
    """`st.`, `kld.` or a storey number, folded to how `Resident` writes it."""
    folded = token.rstrip(".").lower()
    if folded in FLOOR_ALIASES:
        return FLOOR_ALIASES[folded]
    return f"{folded}." if folded.isdigit() else None


@dataclass
class RegisterRow:
    """One person in one unit, mapped but not yet saved."""

    unit_number: str
    name_key: str
    first_name: str = ""
    last_name: str = ""
    alias: str = ""
    email: str = ""
    phone: str = ""
    role: str = ""
    unit_type: str = ""
    resident_number: str = ""
    raw_address: str = ""
    postal_code: str = ""
    city: str = ""
    address: ParsedAddress | None = None
    moved_in: object = None
    is_eligible: bool = False
    source_row: int | None = None

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()


def parse_rows(header, rows, *, first_data_row=2):
    """Map an export into `RegisterRow` objects, skipping what cannot be used.

    `first_data_row` is the line number of `rows[0]` in the file — 2 for a
    normal export, where line 1 is the header. It is recorded for fault-finding.

    A row is skipped when it has no usable `Bolignr.`, because without one it
    has no identity and would arrive afresh on every import. The caretakers'
    `11121` is exactly that case; they are staff, and CLAUDE.md already has them
    getting accounts by hand in the admin.

    Rows that repeat a (unit, name) pair collapse into one, last occurrence
    winning. The export contains genuine duplicates — the same person listed
    twice, once with a typo in their email — and two rows claiming one identity
    is a contradiction the database cannot hold anyway.
    """
    columns = [normalise_header(cell) for cell in header]
    parsed = {}
    skipped = []

    for offset, row in enumerate(rows):
        line = first_data_row + offset
        cells = [str(cell).strip() if cell is not None else "" for cell in row]
        if not any(cells):
            continue
        cells += [""] * (len(columns) - len(cells))

        values = {}
        for index, column in enumerate(columns):
            target = ALIAS_TO_FIELD.get(column)
            if target and not values.get(target):
                values[target] = cells[index]

        unit_number = normalise_number(values.get("unit_number"))
        if not UNIT_NUMBER_RE.fullmatch(unit_number):
            skipped.append(line)
            continue

        key = name_key(values.get("first_name"), values.get("last_name"))
        if not key:
            skipped.append(line)
            continue

        resident_number = normalise_number(values.get("resident_number"))
        parsed[(unit_number, key)] = RegisterRow(
            unit_number=unit_number,
            name_key=key,
            first_name=(values.get("first_name") or "")[:150],
            last_name=(values.get("last_name") or "")[:150],
            alias=(values.get("alias") or "")[:200],
            email=(values.get("email") or "").strip()[:254],
            phone=(values.get("phone") or "")[:32],
            role=(values.get("role") or "")[:64],
            unit_type=(values.get("unit_type") or "")[:64],
            resident_number=(
                resident_number if RESIDENT_NUMBER_RE.fullmatch(resident_number) else ""
            ),
            raw_address=(values.get("raw_address") or "")[:255],
            postal_code=(values.get("postal_code") or "")[:16],
            city=(values.get("city") or "")[:64],
            address=parse_address(values.get("raw_address")),
            moved_in=parse_date(values.get("moved_in")),
            source_row=line,
        )

    register_rows = list(parsed.values())
    mark_eligibility(register_rows)
    return register_rows, skipped


def mark_eligibility(rows):
    """Decide, for the export as a whole, who may activate an account.

    The rule the board settled on is "an andelsbolig and a Beboernr", which
    covers andelshavere, subletters and board members alike — the export gives
    all of them a residential `Enhedstype` and a tenancy number, and it is the
    only question worth asking about the 500-odd rows that matter.

    Household members are the exception that stops it being a one-line check.
    They have neither column filled in, so their eligibility is decided by the
    unit they are listed against: if somebody's `Bolignr.` is a flat that this
    same export shows as residential, they live in that flat. That is why this
    runs over the whole export rather than row by row — and why it is a query
    against the export rather than a hardcoded list of units. A shop with a
    household member listed against it, which the export also contains, is not
    a flat and stays out.

    An address that could not be parsed is never eligible, whatever else the row
    says: the portal would have nowhere to put the residency.
    """
    residential_units = {
        row.unit_number
        for row in rows
        if row.unit_type.strip().lower() in RESIDENTIAL_UNIT_TYPES
        and row.resident_number
        and row.address is not None
    }
    for row in rows:
        if row.address is None:
            row.is_eligible = False
        elif row.unit_number in residential_units and row.resident_number:
            row.is_eligible = True
        else:
            row.is_eligible = (
                row.role.strip().lower() == HOUSEHOLD_ROLE and row.unit_number in residential_units
            )


def unclassified_rows(rows):
    """Rows that look like somebody's home but did not qualify as one.

    A tenancy number and a readable flat address, and still not eligible: that
    is either a genuine oddity — the register does contain a handful of flats
    whose `Enhedstype` nobody filled in — or the first sign that INNA has
    started writing the column differently, in which case the import would
    otherwise report a tidy number and quietly stop letting anyone in.

    Shops and storage rooms are left out: their `Enhedstype` says plainly what
    they are, and a basement shop has a perfectly readable address. What is
    reported is an `Enhedstype` the import recognises as neither — today, the
    handful of flats where the column was simply left blank.

    Worth a line on stderr rather than a rule that guesses. The people in this
    list can still sign up; their request waits for the board, which is what the
    queue is for.
    """
    known = RESIDENTIAL_UNIT_TYPES + NON_RESIDENTIAL_UNIT_TYPES
    return [
        (row.source_row, row.full_name, row.unit_type, row.raw_address)
        for row in rows
        if not row.is_eligible
        and row.resident_number
        and row.address is not None
        and row.unit_type.strip().lower() not in known
    ]


@dataclass
class ImportResult:
    """What an import run did, for the command to report and tests to assert."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    departed: int = 0
    returned: int = 0
    buildings_created: list = field(default_factory=list)
    unparsed_addresses: list = field(default_factory=list)
    unclassified: list = field(default_factory=list)
    skipped_rows: list = field(default_factory=list)

    @property
    def total(self):
        return self.created + self.updated + self.unchanged


#: Fields on `RegisterEntry` that the export owns outright and overwrites on
#: every run. `is_current`, the timestamps and the FK to `Building` are managed
#: separately below; nothing else on the model is the export's to touch.
EXPORT_OWNED_FIELDS = (
    "first_name",
    "last_name",
    "alias",
    "email",
    "phone",
    "role",
    "unit_type",
    "resident_number",
    "raw_address",
    "postal_code",
    "city",
    "floor",
    "door",
    "moved_in",
    "is_eligible",
    "source_row",
)

#: The subset of those whose changing means something. `source_row` is excluded
#: because it is only a pointer into the downloaded file: sorting the export
#: would otherwise report six hundred rows as updated and say nothing at all.
MEANINGFUL_FIELDS = tuple(name for name in EXPORT_OWNED_FIELDS if name != "source_row")


@transaction.atomic
def import_rows(rows, skipped_rows=None):
    """Reconcile parsed rows with the database.

    Atomic, because a register half-replaced is worse than one not replaced —
    and the next run picks it all up regardless.
    """
    result = ImportResult(skipped_rows=list(skipped_rows or []))
    result.unparsed_addresses = [
        (row.source_row, row.raw_address) for row in rows if row.address is None
    ]
    result.unclassified = unclassified_rows(rows)
    buildings = _ensure_buildings(rows, result)
    now = timezone.now()

    existing = {
        (entry.unit_number, entry.name_key): entry
        for entry in RegisterEntry.objects.filter(unit_number__in={row.unit_number for row in rows})
    }

    for row in rows:
        entry = existing.get((row.unit_number, row.name_key))
        address = row.address
        values = {
            "first_name": row.first_name,
            "last_name": row.last_name,
            "alias": row.alias,
            "email": row.email,
            "phone": row.phone,
            "role": row.role,
            "unit_type": row.unit_type,
            "resident_number": row.resident_number,
            "raw_address": row.raw_address,
            "postal_code": row.postal_code,
            "city": row.city,
            "floor": address.floor if address else "",
            "door": address.door if address else "",
            "moved_in": row.moved_in,
            "is_eligible": row.is_eligible,
            "source_row": row.source_row,
        }
        building = buildings.get((address.street, address.house_number)) if address else None

        if entry is None:
            RegisterEntry.objects.create(
                unit_number=row.unit_number,
                name_key=row.name_key,
                building=building,
                first_seen_at=now,
                last_seen_at=now,
                is_current=True,
                **values,
            )
            result.created += 1
            continue

        changed = [name for name in MEANINGFUL_FIELDS if getattr(entry, name) != values[name]]
        if entry.building_id != (building.pk if building else None):
            changed.append("building")
            entry.building = building
        for name in EXPORT_OWNED_FIELDS:
            setattr(entry, name, values[name])
        if not entry.is_current:
            entry.is_current = True
            result.returned += 1
            changed.append("is_current")
        entry.last_seen_at = now
        entry.save(update_fields=[*changed, "source_row", "last_seen_at"])
        if changed:
            result.updated += 1
        else:
            result.unchanged += 1

    result.departed = _mark_departed(rows)
    return result


def _ensure_buildings(rows, result):
    """Create any entrance the export mentions that the portal does not know.

    Buildings come from the register rather than being typed in, so a block the
    association acquires appears by itself on the next import. Created ones are
    reported: a new entrance is either genuine news or a misspelling that has
    just invented a building, and only a person can tell which.
    """
    buildings = {}
    for row in rows:
        if row.address is None:
            continue
        key = (row.address.street, row.address.house_number)
        if key in buildings:
            continue
        building, created = Building.objects.get_or_create(street=key[0], house_number=key[1])
        buildings[key] = building
        if created:
            result.buildings_created.append(str(building))
    return buildings


def _mark_departed(rows):
    """Flag entries the export no longer lists, without deleting anything.

    Somebody who has moved out keeps their row, because their account may own
    bookings and because a disappearance is a question rather than an answer:
    a flat sold, or a dump taken with the wrong filter set. The board sees the
    flag in the admin and decides. What it does do immediately is make them
    ineligible, so a stale row cannot let anyone activate an account.

    `last_seen_at` is deliberately left alone — it records the last export the
    person actually appeared in, which is the useful thing to show beside a row
    that has stopped appearing.

    The comparison runs in Python because identity here is a pair of columns and
    the export is six hundred rows; a tuple `IN` would be clever and slower to
    read for no gain at this size.
    """
    seen = {(row.unit_number, row.name_key) for row in rows}
    stale = [
        entry.pk
        for entry in RegisterEntry.objects.filter(is_current=True).only("unit_number", "name_key")
        if (entry.unit_number, entry.name_key) not in seen
    ]
    if not stale:
        return 0
    return RegisterEntry.objects.filter(pk__in=stale).update(is_current=False, is_eligible=False)


@dataclass
class RegisterMatch:
    """An identity number resolved to one unit of the register.

    `entry` is the single row the residency is built from. Which of a flat's
    two people it is barely matters — they share an address and a tenancy
    number — but it decides nothing else, so it is chosen as helpfully as the
    claim allows: the person whose email was given, failing that the person
    whose tenancy number was given, failing that whoever is listed first.
    """

    entry: RegisterEntry
    entries: list

    @property
    def unit_number(self):
        return self.entry.unit_number


def match_claim(number, email=""):
    """Resolve a claimed identity number to one eligible unit, or None.

    The number is matched against both `Beboernr.` and `Bolignr.`, because the
    board's rule admits people who have only one of them. A household member
    has no tenancy number at all and can only offer their flat's — either the
    unit number, or the tenancy number printed on the andelshaver's statement,
    which resolves to the same flat. Both work, and both land on the same unit.

    Ambiguity is a refusal, not a guess. If a number somehow reaches two
    different units, no residency is attached and the request goes to the board
    — which is the behaviour the whole signup flow falls back on anyway.
    """
    cleaned = normalise_number(number)
    if not cleaned:
        return None

    candidates = list(
        RegisterEntry.objects.eligible()
        .filter(Q(resident_number=cleaned) | Q(unit_number=cleaned))
        .select_related("building")
        .order_by("pk")
    )
    if not candidates:
        return None

    units = {entry.unit_number for entry in candidates}
    if len(units) != 1:
        return None

    entries = list(
        RegisterEntry.objects.eligible()
        .filter(unit_number=units.pop())
        .select_related("building")
        .order_by("pk")
    )
    chosen = None
    if email:
        chosen = next(
            (entry for entry in entries if entry.email.lower() == email.strip().lower()), None
        )
    if chosen is None:
        chosen = next((entry for entry in entries if entry.resident_number == cleaned), None)
    return RegisterMatch(entry=chosen or entries[0], entries=entries)


#: Written into `SignupRequest.review_note` when the register decided instead of
#: a person, so the board's queue does not look as though somebody approved it
#: and forgot to say who.
AUTO_APPROVED_NOTE = _(
    "Godkendt automatisk: det oplyste nummer passer på %(name)s, %(address)s "
    "(bolignr. %(unit)s) i beboerregistret."
)


def auto_approve(signup_request):
    """Activate a signup that the register vouches for, or leave it to the board.

    The board's rule is that living in an andelsbolig is what entitles somebody
    to the portal, and the register is the only place that fact exists — so when
    a claimed number resolves to one eligible unit, there is nothing left for a
    human to check and the account is activated on the spot, residency and all.
    Everything else falls through to the queue exactly as before: an unmatched
    number, a number that reaches two units, a shop, a storage room, a person
    whose row stopped appearing in the export.

    **The claimed email is deliberately not part of the test.** It cannot be:
    thirty andelsboliger have no email in the register at all, and the register
    keeps whichever address INNA was last given rather than the one somebody
    signs up with. The number is what is being checked, and what it proves is
    that the claimant has seen a rent statement for a flat in the association.
    That is a weaker proof than a board member comparing a name to the register,
    and it is the trade the board chose in exchange for not making five hundred
    residents wait on a queue. It is also why the response says nothing about
    the outcome — see `SignupView`.

    Returns the `Resident` row it created, or None if nothing was done.
    """
    if not signup_request.is_pending or signup_request.user is None:
        return None
    match = match_claim(signup_request.claimed_resident_number, email=signup_request.email)
    if match is None:
        return None

    resident = attach_residency(signup_request.user, match.entry)
    if resident is None:
        # An eligible entry always has a building — `mark_eligibility` refuses a
        # row whose address could not be read — so this is unreachable rather
        # than expected. Falling back to the queue is the safe way to be wrong.
        return None
    signup_request.approve(
        note=str(AUTO_APPROVED_NOTE)
        % {
            "name": match.entry.full_name,
            "address": match.entry.address,
            "unit": match.entry.unit_number,
        }
    )
    return resident


def pending_requests_to_approve():
    """Pending signups that the register would now let through.

    Run after an import: somebody who signed up the week before their flat
    appeared in the export should not sit in the queue forever because the two
    happened in the wrong order.
    """
    return SignupRequest.objects.filter(
        status=SignupRequestStatus.PENDING, user__isnull=False
    ).select_related("user")


def attach_residency(user, entry):
    """Give a user the residency a register entry describes.

    Idempotent, and never destructive: a user who already has a `Resident` row
    has it updated in place rather than replaced, so a residency a board member
    corrected by hand survives the next import touching the same person.
    """
    if entry.building is None:
        return None
    values = {
        "unit_number": entry.unit_number,
        "resident_number": entry.resident_number,
        "building": entry.building,
        "floor": entry.floor,
        "door": entry.door,
    }
    resident = Resident.objects.filter(user=user).first()
    if resident is None:
        return Resident.objects.create(user=user, **values)
    for name, value in values.items():
        setattr(resident, name, value)
    resident.save(update_fields=list(values))
    return resident


# --------------------------------------------------------------------------
# Running an import, and saying what it did
#
# Both callers — `manage.py import_residents` and the admin's upload page — go
# through here, so a board member uploading a file and a developer with a shell
# see the same diagnostics about the same file. The Danish lives here rather
# than in either caller for exactly that reason.
# --------------------------------------------------------------------------


#: How loudly to say a thing. `warning` is not an error: every real import
#: produces a few, because storage rooms have unreadable addresses and the
#: caretakers have no unit number.
SUCCESS, INFO, WARNING = "success", "info", "warning"


@dataclass
class Note:
    """One line of an import's report."""

    level: str
    text: str


@dataclass
class ImportOutcome:
    """Everything one run of the import changed."""

    result: ImportResult
    approved: list = field(default_factory=list)
    linked: list = field(default_factory=list)


@transaction.atomic
def run_import(header, rows, *, first_data_row=2):
    """Import an export and follow through on what it implies.

    One transaction over all three steps. The follow-through is not a separate
    convenience: a register that has just learnt about a flat, and a pending
    signup for that flat still sitting in the queue, is a state nobody should
    have to notice and act on by hand.
    """
    parsed, skipped = parse_rows(header, rows, first_data_row=first_data_row)
    outcome = ImportOutcome(result=import_rows(parsed, skipped))
    outcome.approved = approve_pending_requests()
    outcome.linked = link_users_without_residency()
    return outcome


def approve_pending_requests():
    """Let through any pending signup the register now vouches for.

    Somebody who signed up the week before their flat reached the export should
    not wait in the queue for a human to notice that it has since arrived.
    Returns the email addresses approved.
    """
    return [
        request.email
        for request in pending_requests_to_approve()
        if auto_approve(request) is not None
    ]


def link_users_without_residency():
    """Give a residency to accounts that predate the register.

    Matched on email, only where exactly one eligible entry has it, and only for
    users with no `Resident` row at all — a residency somebody corrected by hand
    is not the import's to overwrite. These are accounts a board member created
    themselves, so the address is what was missing, not the vouching.

    Two entries sharing an address is one person listed against two units, which
    is a question for the board rather than an address to guess at.
    """
    linked = []
    for user in User.objects.filter(resident__isnull=True).exclude(email=""):
        matches = list(RegisterEntry.objects.eligible().filter(email__iexact=user.email)[:2])
        if len(matches) != 1 or attach_residency(user, matches[0]) is None:
            continue
        linked.append(user.email)
    return linked


def header_notes(header):
    """What the portal made of the columns. Never fatal on its own."""
    unknown = unrecognised_headers(header)
    if not unknown:
        return []
    return [
        Note(INFO, _("Kolonner uden betydning for portalen, ignoreret: %s.") % ", ".join(unknown))
    ]


def dry_run_notes(rows, skipped):
    """The report for a run that wrote nothing."""
    eligible = sum(1 for row in rows if row.is_eligible)
    notes = [
        Note(
            SUCCESS,
            _(
                "Prøvekørsel: %(rows)d rækker læst, %(eligible)d kan aktivere en konto, "
                "%(skipped)d linjer sprunget over. Intet er gemt."
            )
            % {"rows": len(rows), "eligible": eligible, "skipped": len(skipped)},
        )
    ]
    return notes + diagnostic_notes(
        skipped_rows=skipped,
        unparsed_addresses=[
            (row.source_row, row.raw_address) for row in rows if row.address is None
        ],
        unclassified=unclassified_rows(rows),
    )


def outcome_notes(outcome):
    """The report for a run that wrote."""
    result = outcome.result
    notes = [
        Note(
            SUCCESS,
            _(
                "%(created)d nye, %(updated)d opdaterede, %(unchanged)d uændrede "
                "(%(total)d i alt). %(eligible)d kan aktivere en konto."
            )
            % {
                "created": result.created,
                "updated": result.updated,
                "unchanged": result.unchanged,
                "total": result.total,
                "eligible": RegisterEntry.objects.eligible().count(),
            },
        )
    ]
    if result.returned:
        notes.append(Note(INFO, _("%d række(r) er dukket op i registret igen.") % result.returned))
    if outcome.approved:
        notes.append(
            Note(
                SUCCESS,
                _(
                    "%(count)d ventende tilmelding(er) godkendt automatisk, fordi registret nu "
                    "kender dem: %(emails)s"
                )
                % {"count": len(outcome.approved), "emails": ", ".join(outcome.approved)},
            )
        )
    if outcome.linked:
        notes.append(
            Note(
                SUCCESS,
                _("%(count)d eksisterende konto(er) har fået tilknyttet en bolig: %(emails)s")
                % {"count": len(outcome.linked), "emails": ", ".join(outcome.linked)},
            )
        )
    if result.departed:
        notes.append(
            Note(
                WARNING,
                _(
                    "%d række(r) står ikke længere i registret og er markeret som fraflyttet. "
                    "Deres konti er urørte — se dem efter, og deaktivér dem, der er flyttet."
                )
                % result.departed,
            )
        )
    if result.buildings_created:
        notes.append(
            Note(
                WARNING,
                _(
                    "Nye opgange oprettet: %s. Tjek at de er rigtige og ikke en stavefejl i en "
                    "adresse."
                )
                % ", ".join(result.buildings_created),
            )
        )
    return notes + diagnostic_notes(
        skipped_rows=result.skipped_rows,
        unparsed_addresses=result.unparsed_addresses,
        unclassified=result.unclassified,
    )


def diagnostic_notes(*, skipped_rows=(), unparsed_addresses=(), unclassified=()):
    """The three warnings every import can produce, worded once.

    All three are normal in small numbers and alarming in large ones, so each
    says what a long list would mean — that is the whole value of reading them.
    """
    notes = []
    if skipped_rows:
        notes.append(
            Note(
                WARNING,
                _(
                    "Sprang linje(r) %s over: intet brugbart bolignr., så rækken kan ikke få en "
                    "stabil identitet. Viceværterne står sådan i registret og hører hjemme som "
                    "almindelige medarbejderkonti i stedet."
                )
                % ", ".join(str(line) for line in skipped_rows),
            )
        )
    if unparsed_addresses:
        notes.append(
            Note(
                WARNING,
                _(
                    "%(count)d adresse(r) kunne ikke læses som en bolig og kan derfor ikke "
                    "aktivere en konto: %(shown)s. Kælder- og depotrum hører til her; en "
                    "lejlighed gør ikke."
                )
                % {
                    "count": len(unparsed_addresses),
                    # No `repr()`: its quotes come out as `&#x27;` in the admin,
                    # and the addresses are stripped, so there is nothing for
                    # quoting to reveal.
                    "shown": _elided(
                        f"linje {line}: {address}" for line, address in unparsed_addresses
                    ),
                },
            )
        )
    if unclassified:
        notes.append(
            Note(
                WARNING,
                _(
                    "%(count)d række(r) har beboernr. og en læsbar boligadresse, men tæller ikke "
                    "som en bolig: %(shown)s. De kan stadig tilmelde sig — deres anmodning "
                    "venter bare på bestyrelsen. Er listen lang, har registret skiftet navn på "
                    "enhedstyperne."
                )
                % {
                    "count": len(unclassified),
                    "shown": _elided(
                        f"linje {line}: {name} ({unit_type or 'ingen enhedstype'}), {address}"
                        for line, name, unit_type, address in unclassified
                    ),
                },
            )
        )
    return notes


def _elided(items, limit=10):
    """Join a report's examples, keeping it to a line somebody will read."""
    shown = list(items)
    joined = "; ".join(shown[:limit])
    return f"{joined} (+{len(shown) - limit} flere)" if len(shown) > limit else joined
