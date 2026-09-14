"""
Importing INNA's resident register, and what it is allowed to decide.

Two properties are worth more than the rest of this file put together.

*Who the register lets in.* Eligibility is what stands between a stranger and
the booking calendar now that nobody has to click approve, so the cases that
must stay out — a shop, a storage room, the partner of a shopkeeper — are
tested as carefully as the cases that must get in.

*What an import must not touch.* The register is downloaded and re-imported
whenever somebody remembers to, so a run that trampled a board member's
correction, deleted somebody who had moved, or handed out a second account
would be a run nobody could safely repeat. `test_reimporting_*` are the ones to
keep green.
"""

import csv
from datetime import date
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.urls import reverse

from apps.accounts.models import (
    Building,
    RegisterEntry,
    Resident,
    SignupRequest,
    SignupRequestStatus,
)
from apps.accounts.register import (
    attach_residency,
    auto_approve,
    import_rows,
    match_claim,
    parse_address,
    parse_rows,
)

User = get_user_model()

SAMPLE = Path(__file__).parent / "data" / "register_sample.csv"


def read_sample(path=SAMPLE):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    return rows[0], rows[1:]


@pytest.fixture(autouse=True)
def clear_throttle_state():
    """Signup is throttled per IP, and the history outlives a single test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def register(db):
    """The sample register, imported. Returns the import's own result."""
    header, rows = read_sample()
    parsed, skipped = parse_rows(header, rows)
    return import_rows(parsed, skipped)


def entry(name):
    return RegisterEntry.objects.get(first_name=name)


# --------------------------------------------------------------------------
# Reading the export
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Jægersborggade 5, 1. tv.", ("Jægersborggade", "5", "1.", "tv")),
        ("Jægersborggade 5, 3.", ("Jægersborggade", "5", "3.", "")),
        ("Hørsholmsgade 34, kld. tv.", ("Hørsholmsgade", "34", "kld.", "tv")),
        ("Jægersborggade 2, st. th.", ("Jægersborggade", "2", "st.", "th")),
        ("Ndr. Fasanvej 138 B, 4. th.", ("Ndr. Fasanvej", "138B", "4.", "th")),
        # Noise that is actually in the export.
        ("Jægersborggade  43, 4. th.", ("Jægersborggade", "43", "4.", "th")),
        ("Jægersborggade 16, 4. tv", ("Jægersborggade", "16", "4.", "tv")),
        ("Jægersborggade 16, 4. t.v", ("Jægersborggade", "16", "4.", "tv")),
        ("Jægersborggade 7, 3. sal", ("Jægersborggade", "7", "3.", "")),
        ("Jægersborggade 5, 2. tv. Sammenlagt med LM 5507", ("Jægersborggade", "5", "2.", "tv")),
    ],
)
def test_an_address_is_read_into_a_street_a_number_a_floor_and_a_door(raw, expected):
    parsed = parse_address(raw)
    assert (parsed.street, parsed.house_number, parsed.floor, parsed.door) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "Jægersborggade - 3668",  # a storage room, numbered not addressed
        "Jægersborggade, nr. 3520",
        "Jægersborggade 27, nedre kld nr. 409",
        "Jægersborggade 46, Rum I",
        "Jægersborggade 34",  # no flat at all — the caretakers' office
        "",
    ],
)
def test_an_address_that_is_not_a_flat_is_refused_rather_than_guessed_at(raw):
    """Storage rooms outnumber the mistakes here, and both must fail the same way.

    A guess would be worse than a refusal: the address decides which flat a
    residency is attached to, and an invented one is a resident registered to
    somebody else's home.
    """
    assert parse_address(raw) is None


def test_a_row_without_a_usable_unit_number_is_skipped_and_reported():
    """The caretakers, whose Bolignr. is the association's number and not a unit."""
    header, rows = read_sample()
    parsed, skipped = parse_rows(header, rows)

    assert skipped, "the caretaker row should have been skipped"
    assert "Jonas" not in {row.first_name for row in parsed}


def test_a_row_repeated_in_the_export_becomes_one_entry(register):
    """The export really does contain the same person twice, once with a typo."""
    assert RegisterEntry.objects.filter(first_name="Mette").count() == 1
    assert entry("Mette").phone == "+4520000013", "the last occurrence should win"


def test_the_move_in_date_is_read_but_decides_nothing(register):
    """Somebody taking over a flat in December may book the room in November."""
    future = entry("Mette")
    assert future.moved_in == date(2026, 12, 1)
    assert future.is_eligible is True


# --------------------------------------------------------------------------
# Who may activate an account
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("Ane", "an andelshaver with a tenancy number"),
        ("Bo", "the second person on the same tenancy number"),
        ("Erik", "a subletter — they live here too"),
        ("Frida", "Enhedstype typed 'Bolig' rather than 'Andelsbolig'"),
        ("Gitte", "a board member, and an unusual tenancy-number shape"),
        ("Ditte", "a household member: no tenancy number, but the flat is residential"),
    ],
)
def test_people_who_live_in_a_flat_may_activate_an_account(register, name, why):
    assert entry(name).is_eligible is True, why


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("Kaffebaren", "a shop is not a home"),
        ("Hanne", "the shopkeeper's household member — still not a home"),
        ("Ib", "a storage room, and an address that cannot be read"),
        ("Lise", "no Enhedstype and no other row showing the unit to be residential"),
    ],
)
def test_people_who_do_not_live_in_a_flat_may_not(register, name, why):
    assert entry(name).is_eligible is False, why


def test_a_household_member_is_judged_by_the_flat_and_not_by_their_own_row(register):
    """The rule that needs the whole export to decide, stated on its own.

    Ditte and Hanne have identical rows as far as their own columns go — no
    Enhedstype, no tenancy number, `Husstandsmedlem`. What separates them is
    that Ditte's unit has an andelsbolig row against it and Hanne's is a shop.
    """
    ditte, hanne = entry("Ditte"), entry("Hanne")

    assert (ditte.unit_type, ditte.resident_number) == (hanne.unit_type, hanne.resident_number)
    assert ditte.is_eligible is True
    assert hanne.is_eligible is False


# --------------------------------------------------------------------------
# Importing
# --------------------------------------------------------------------------


def test_importing_creates_the_entrances_the_register_mentions(register):
    assert set(Building.objects.values_list("street", "house_number")) == {
        ("Jægersborggade", "5"),
        ("Jægersborggade", "43"),
        ("Jægersborggade", "16"),
        ("Jægersborggade", "7"),
        ("Jægersborggade", "57"),
        ("Jægersborggade", "42"),
        ("Stefansgade", "41"),
    }
    assert "Jægersborggade 5" in register.buildings_created


def test_importing_creates_no_accounts(register):
    """The register is the list a claim is checked against, not a list of logins."""
    assert User.objects.count() == 0
    assert Resident.objects.count() == 0


def test_reimporting_the_same_export_changes_nothing(register, db):
    header, rows = read_sample()
    parsed, skipped = parse_rows(header, rows)
    second = import_rows(parsed, skipped)

    assert (second.created, second.updated) == (0, 0)
    assert second.unchanged == register.total
    assert second.buildings_created == []


def test_reimporting_does_not_disturb_a_residency_corrected_by_hand(register, db):
    """A board member's fix must survive the next import.

    The import owns the register; the portal owns `Resident`. Nothing in an
    import run reaches across that line, which is what makes it safe to re-run
    without reading the file first.
    """
    user = User.objects.create_user(email="ane.bang@example.dk", password="hemmeligt123")
    attach_residency(user, entry("Ane"))
    resident = user.resident
    resident.door = "mf"
    resident.save(update_fields=["door"])

    header, rows = read_sample()
    parsed, skipped = parse_rows(header, rows)
    import_rows(parsed, skipped)

    resident.refresh_from_db()
    assert resident.door == "mf"


def test_a_person_missing_from_a_later_export_is_flagged_and_not_deleted(register, db):
    """A move-out is a question for the board, and their bookings must survive it."""
    header, rows = read_sample()
    remaining = [row for row in rows if row[0] != "Erik"]
    parsed, skipped = parse_rows(header, remaining)
    result = import_rows(parsed, skipped)

    assert result.departed == 1
    gone = entry("Erik")
    assert gone.is_current is False
    assert gone.is_eligible is False, "a stale row must stop admitting people at once"


def test_someone_who_reappears_is_current_again(register, db):
    header, rows = read_sample()
    import_rows(*parse_rows(header, [row for row in rows if row[0] != "Erik"]))
    result = import_rows(*parse_rows(header, rows))

    assert result.returned == 1
    assert entry("Erik").is_eligible is True


# --------------------------------------------------------------------------
# Matching a claimed number
# --------------------------------------------------------------------------


def test_a_tenancy_number_matches_the_flat_it_belongs_to(register):
    match = match_claim("1-1121-4203-2")
    assert match is not None
    assert match.unit_number == "1-1121-4203"
    assert match.entry.address == "Jægersborggade 5, 1. tv"


def test_a_unit_number_matches_too(register):
    """What a household member has to offer: their flat, not a tenancy."""
    match = match_claim("1-1121-4208")
    assert match is not None
    assert match.unit_number == "1-1121-4208"


def test_a_household_member_can_claim_the_tenancy_number_of_their_own_flat(register):
    """Ditte has no number of her own; the one on the statement is Cecilie's."""
    match = match_claim("1-1121-4208-2", email="ditte.ege@example.dk")
    assert match is not None
    assert match.entry.first_name == "Ditte", "the claimed email should pick the person"


def test_a_shared_tenancy_number_still_resolves_to_one_flat(register):
    """Ane and Bo share a number. That is one home, so it is not an ambiguity."""
    match = match_claim("1-1121-4203-2")
    assert {person.first_name for person in match.entries} == {"Ane", "Bo"}


@pytest.mark.parametrize(
    ("number", "why"),
    [
        ("1-1121-5301-2", "the shop's tenancy number"),
        ("1-1121-3668-2", "a storage room"),
        ("1-1121-9999-9", "a number that is in no register at all"),
        ("", "nothing typed"),
        ("ikke et nummer", "not a number"),
    ],
)
def test_a_number_the_register_cannot_vouch_for_matches_nothing(register, number, why):
    assert match_claim(number) is None, why


# --------------------------------------------------------------------------
# Signup, now that the register can answer
# --------------------------------------------------------------------------


VALID = {
    "email": "ny.beboer@example.dk",
    "first_name": "Ny",
    "last_name": "Beboer",
    "phone": "12345678",
    "address": "Jægersborggade 5, 1. tv.",
    "resident_number": "1-1121-4203-2",
    "password": "vinter-cykel-lampe-42",
}


def signup(client, **overrides):
    return client.post(
        reverse("accounts:signup"), {**VALID, **overrides}, content_type="application/json"
    )


def test_a_signup_the_register_vouches_for_is_active_at_once(client, register):
    response = signup(client)

    assert response.status_code == 202
    user = User.objects.get(email=VALID["email"])
    assert user.is_active is True
    assert user.resident.address == "Jægersborggade 5, 1. tv"
    assert user.resident.unit_number == "1-1121-4203"
    assert user.resident.resident_number == "1-1121-4203-2"


def test_an_automatic_approval_records_what_it_matched(client, register):
    """An approval with no reviewer must never look like somebody forgot to sign."""
    signup(client)
    approved = SignupRequest.objects.get()

    assert approved.status == SignupRequestStatus.APPROVED
    assert approved.reviewed_by is None
    assert "1-1121-4203" in approved.review_note
    assert "Jægersborggade 5" in approved.review_note


def test_a_matched_resident_can_log_in_immediately(client, register):
    signup(client)
    response = client.post(
        reverse("accounts:login"),
        {"email": VALID["email"], "password": VALID["password"]},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["resident"]["address"] == "Jægersborggade 5, 1. tv"


@pytest.mark.parametrize(
    ("number", "why"),
    [
        ("1-1121-5301-2", "the shop's tenancy number"),
        ("1-1121-3668-2", "a storage room"),
        ("1-1121-9999-9", "a number nobody has"),
        ("1-1121-4203-3", "one digit out — the board reads it, the register cannot"),
    ],
)
def test_a_signup_the_register_cannot_vouch_for_waits_for_the_board(client, register, number, why):
    """Everything the register cannot answer falls back to the queue, as before."""
    signup(client, resident_number=number)

    user = User.objects.get(email=VALID["email"])
    assert user.is_active is False
    assert user.is_resident is False
    assert SignupRequest.objects.get().status == SignupRequestStatus.PENDING


def test_signup_answers_the_same_whether_the_register_knew_the_number_or_not(client, register):
    """The uniform answer is the whole reason signup cannot be used as an oracle.

    A 201 on a match and a 202 otherwise would turn the form into a way to test
    which resident numbers are real, which is exactly what the response has
    always refused to say.
    """
    matched = signup(client)
    unmatched = signup(client, email="anden@example.dk", resident_number="1-1121-9999-9")

    assert matched.status_code == unmatched.status_code == 202
    assert matched.json() == unmatched.json()


def test_signing_up_without_a_register_at_all_still_works(client, db):
    """The board must be able to run the portal before the first import."""
    response = signup(client)

    assert response.status_code == 202
    assert User.objects.get(email=VALID["email"]).is_active is False
    assert SignupRequest.objects.get().status == SignupRequestStatus.PENDING


def test_a_pending_request_is_approved_once_the_register_catches_up(client, db):
    """Signing up the week before the flat reaches the export must not strand anyone."""
    signup(client)
    pending = SignupRequest.objects.get()
    assert pending.status == SignupRequestStatus.PENDING

    header, rows = read_sample()
    import_rows(*parse_rows(header, rows))
    auto_approve(pending)

    pending.refresh_from_db()
    assert pending.status == SignupRequestStatus.APPROVED
    assert User.objects.get(email=VALID["email"]).is_active is True


# --------------------------------------------------------------------------
# The management command
# --------------------------------------------------------------------------


def test_the_command_imports_the_file(db, capsys):
    call_command("import_residents", str(SAMPLE))

    assert RegisterEntry.objects.count() == 12, "14 rows, less a caretaker and a duplicate"
    assert RegisterEntry.objects.eligible().count() == 8
    assert "kan aktivere en konto" in capsys.readouterr().out


def test_a_dry_run_writes_nothing(db, capsys):
    call_command("import_residents", str(SAMPLE), "--dry-run")

    assert RegisterEntry.objects.count() == 0
    assert "Prøvekørsel" in capsys.readouterr().out


def test_the_command_approves_the_signups_the_register_now_knows(client, db, capsys):
    signup(client)
    call_command("import_residents", str(SAMPLE))

    assert User.objects.get(email=VALID["email"]).is_active is True
    assert "godkendt automatisk" in capsys.readouterr().out


def test_the_command_gives_a_residency_to_an_account_that_predates_the_register(db, capsys):
    """Accounts a board member made by hand were missing an address, not a login."""
    user = User.objects.create_user(email="ane.bang@example.dk", password="hemmeligt123")
    call_command("import_residents", str(SAMPLE))

    user.refresh_from_db()
    assert user.resident.address == "Jægersborggade 5, 1. tv"
    assert "tilknyttet en bolig" in capsys.readouterr().out


def test_the_command_refuses_a_file_that_is_not_the_register(db, tmp_path):
    """A renamed column is the failure that otherwise looks like success."""
    wrong = tmp_path / "noget-andet.csv"
    wrong.write_text("Navn,Email\nAne Bang,ane@example.dk\n", encoding="utf-8")

    with pytest.raises(CommandError, match="mangler de kolonner"):
        call_command("import_residents", str(wrong))


def test_the_command_refuses_a_file_that_is_not_there(db):
    with pytest.raises(CommandError, match="findes ikke"):
        call_command("import_residents", "data/register/ikke-en-fil.csv")


def test_the_command_reports_a_flat_the_rule_did_not_recognise_as_one(db, capsys):
    """Lise: a tenancy number, a real address, and no Enhedstype to go on.

    She waits for the board, which is fine. What is not fine is nobody knowing,
    because the same warning is what a renamed Enhedstype column would trip.
    """
    call_command("import_residents", str(SAMPLE))

    assert "Lise Munk" in capsys.readouterr().err
