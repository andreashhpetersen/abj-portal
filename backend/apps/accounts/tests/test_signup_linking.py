"""
Approving a signup against a register entry chosen by hand.

The case this exists for: a resident types their Beboernr. with two digits
transposed, `match_claim` correctly refuses to guess, and the request lands in
the board's queue. Somebody on the board recognises the name. Approving from
the changelist would activate the account and leave it with no address at all —
nothing in the portal gates on residency, so the omission is invisible until
months later, when a booking cannot be placed in a flat.

Three properties are worth guarding. The account must come out of it with the
residency of the row that was chosen, not the one that was claimed. What the
applicant typed must survive untouched, with the difference written down —
otherwise the only record that anything was corrected is gone. And the picker
must not offer a way round the register's own rule: a shop, or somebody who has
moved out, is not a typo.
"""

import csv
from pathlib import Path

import pytest
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.accounts.models import (
    RegisterEntry,
    Resident,
    SignupRequest,
    SignupRequestStatus,
    User,
)
from apps.accounts.register import import_rows, parse_rows, search_eligible

SAMPLE = Path(__file__).parent / "data" / "register_sample.csv"

#: Gitte Holm is a board member at Jægersborggade 7, 3. sal in the sample —
#: unit 1-1121-409, tenancy 1-1121-409-2, and eligible. The claim below
#: transposes two digits of it, which is the whole scenario.
CLAIMED_NUMBER = "1-1121-490"

CLAIM = {
    "email": "gitte.holm@example.dk",
    "first_name": "Gitte",
    "last_name": "Holm",
    "phone": "",
    "address": "Jægersborggade 7, 3. sal",
    "resident_number": CLAIMED_NUMBER,
    "password": "vinter-cykel-lampe-42",
}


@pytest.fixture(autouse=True)
def clear_throttle_state():
    """Throttle history lives in the cache, which outlives a single test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def register(db):
    """The sample register, imported."""
    with SAMPLE.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    return import_rows(*parse_rows(rows[0], rows[1:]))


@pytest.fixture
def claim(register):
    """A pending request from somebody who mistyped their own number.

    Made through the real signup endpoint rather than by creating the rows:
    that a number one digit out produces a pending request and an inactive
    account is the premise of everything below, and it is cheap to prove rather
    than assume.
    """
    response = Client().post(reverse("accounts:signup"), CLAIM, content_type="application/json")
    assert response.status_code == 202
    signup_request = SignupRequest.objects.get(email=CLAIM["email"])
    assert signup_request.status == SignupRequestStatus.PENDING
    assert not signup_request.user.is_active
    return signup_request


@pytest.fixture
def board(db, client):
    """A board member who may decide signup requests, logged into the admin."""
    user = User.objects.create_user(
        email="bestyrelse@example.dk",
        password="hemmeligt123",
        first_name="Bodil",
        last_name="Bestyrelse",
        is_staff=True,
    )
    user.user_permissions.add(
        Permission.objects.get(codename="change_signuprequest"),
        Permission.objects.get(codename="view_signuprequest"),
    )
    client.force_login(user)
    return client


def link_url(signup_request):
    return reverse("admin:accounts_signuprequest_link", args=[signup_request.pk])


def entry(**lookup):
    return RegisterEntry.objects.get(**lookup)


def confirm(client, signup_request, chosen, *, note="", query="holm"):
    return client.post(
        link_url(signup_request),
        {"query": query, "entry": str(chosen.pk), "note": note, "confirm": "1"},
        follow=True,
    )


# --------------------------------------------------------------------------
# Approving against the right row
# --------------------------------------------------------------------------


def test_a_board_member_can_approve_a_mistyped_number_against_the_right_entry(board, claim):
    gitte = entry(unit_number="1-1121-409")

    response = confirm(board, claim, gitte)

    assert response.status_code == 200
    claim.refresh_from_db()
    assert claim.status == SignupRequestStatus.APPROVED
    assert claim.reviewed_by.email == "bestyrelse@example.dk"
    assert claim.user.is_active


def test_the_account_gets_the_residency_of_the_row_that_was_chosen(board, claim):
    gitte = entry(unit_number="1-1121-409")

    confirm(board, claim, gitte)

    resident = Resident.objects.get(user__email=CLAIM["email"])
    assert resident.unit_number == "1-1121-409"
    assert resident.resident_number == "1-1121-409-2"
    assert str(resident.building) == "Jægersborggade 7"
    assert resident.floor == "3."


def test_the_note_records_what_was_claimed_and_what_it_was_linked_to(board, claim):
    """The one place the correction exists, so both halves have to be in it."""
    gitte = entry(unit_number="1-1121-409")

    confirm(board, claim, gitte, note="Kender hende fra bestyrelsen.")

    claim.refresh_from_db()
    assert CLAIMED_NUMBER in claim.review_note
    assert "1-1121-409" in claim.review_note
    assert "Jægersborggade 7" in claim.review_note
    assert "Bodil Bestyrelse" in claim.review_note
    assert "Kender hende fra bestyrelsen." in claim.review_note


def test_the_claimed_number_is_left_exactly_as_the_applicant_typed_it(board, claim):
    """What was submitted is the thing that was checked — correcting it in
    place would destroy the only record of it."""
    confirm(board, claim, entry(unit_number="1-1121-409"))

    claim.refresh_from_db()
    assert claim.claimed_resident_number == CLAIMED_NUMBER
    assert claim.claimed_address == CLAIM["address"]


def test_the_applicant_is_told_their_account_is_live(board, claim):
    """The same mail a plain approval sends: from the applicant's side nothing
    unusual happened, and nothing should say otherwise."""
    mail.outbox.clear()

    confirm(board, claim, entry(unit_number="1-1121-409"))

    assert [message.to for message in mail.outbox] == [[CLAIM["email"]]]


# --------------------------------------------------------------------------
# What the page must not do
# --------------------------------------------------------------------------


def test_the_page_opens_with_the_applicants_name_already_searched(board, claim):
    """The claimed number is known not to resolve by the time anybody is here,
    so the name is the only search worth starting from."""
    response = board.get(link_url(claim))
    page = response.content.decode()

    assert response.status_code == 200
    assert CLAIMED_NUMBER in page
    assert "Jægersborggade 7" in page


def test_searching_approves_nothing(board, claim):
    """Searching and choosing share a form, so the search must be inert."""
    response = board.post(link_url(claim), {"query": "holm", "search": "1"})

    assert response.status_code == 200
    claim.refresh_from_db()
    assert claim.status == SignupRequestStatus.PENDING
    assert not claim.user.is_active


def test_confirming_without_choosing_anybody_is_an_error_not_an_approval(board, claim):
    response = board.post(link_url(claim), {"query": "holm", "confirm": "1"})

    assert response.status_code == 200
    claim.refresh_from_db()
    assert claim.status == SignupRequestStatus.PENDING
    assert Resident.objects.filter(user__email=CLAIM["email"]).count() == 0


def test_a_row_the_register_would_not_admit_cannot_be_chosen(board, claim):
    """The shop is in the register and deliberately ineligible. A picker that
    would take it is a way round the rule, not a way round a typo."""
    shop = entry(unit_number="1-1121-5301", last_name="ApS")

    response = confirm(board, claim, shop, query="kaffebaren")

    assert response.status_code == 200
    claim.refresh_from_db()
    assert claim.status == SignupRequestStatus.PENDING
    assert Resident.objects.filter(user__email=CLAIM["email"]).count() == 0


def test_a_request_that_has_already_been_decided_cannot_be_linked(board, claim):
    claim.approve(by=None)

    response = board.get(link_url(claim), follow=True)

    assert response.status_code == 200
    assert Resident.objects.filter(user__email=CLAIM["email"]).count() == 0


def test_a_board_member_without_the_change_permission_is_refused(db, client, claim):
    intruder = User.objects.create_user(
        email="nysgerrig@example.dk", password="hemmeligt123", is_staff=True
    )
    intruder.user_permissions.add(Permission.objects.get(codename="view_signuprequest"))
    client.force_login(intruder)

    response = client.post(
        link_url(claim),
        {"query": "holm", "entry": str(entry(unit_number="1-1121-409").pk), "confirm": "1"},
    )

    assert response.status_code == 403
    claim.refresh_from_db()
    assert claim.status == SignupRequestStatus.PENDING


# --------------------------------------------------------------------------
# The action that gets you there
# --------------------------------------------------------------------------


def run_action(client, requests):
    return client.post(
        reverse("admin:accounts_signuprequest_changelist"),
        {
            "action": "link_selected_to_register",
            "_selected_action": [str(request.pk) for request in requests],
        },
    )


def test_the_action_opens_the_page_for_the_one_request_selected(board, claim):
    response = run_action(board, [claim])

    assert response.status_code == 302
    assert response["Location"] == link_url(claim)


def test_the_action_refuses_a_selection_of_several(board, claim, register):
    """There is no bulk version of recognising somebody."""
    other = SignupRequest.objects.create(
        email="anden@example.dk",
        user=User.objects.create_user(
            email="anden@example.dk", password="hemmeligt123", is_active=False
        ),
        claimed_address="Stefansgade 41, 2. mf.",
        claimed_resident_number="1-1121-6608-2",
    )

    response = run_action(board, [claim, other])

    # Django sends an action that returns nothing back to the changelist, where
    # the refusal is waiting as a message.
    assert response["Location"] == reverse("admin:accounts_signuprequest_changelist")
    for signup_request in (claim, other):
        signup_request.refresh_from_db()
        assert signup_request.status == SignupRequestStatus.PENDING


# --------------------------------------------------------------------------
# The search itself
# --------------------------------------------------------------------------


def test_every_search_term_has_to_match(register):
    """Several words narrow the result. A search that widened with each word
    typed would be useless on a register where half the rows share a street."""
    assert [found.full_name for found in search_eligible("holm")] == ["Gitte Holm"]
    assert list(search_eligible("holm jægersborggade")) != []
    assert list(search_eligible("holm stefansgade")) == []


def test_a_search_finds_the_number_the_applicant_should_have_typed(register):
    found = list(search_eligible("1-1121-409"))

    assert [entry.full_name for entry in found] == ["Gitte Holm"]


def test_an_empty_search_offers_nobody(register):
    """Six hundred radio buttons is not a picker."""
    assert list(search_eligible("")) == []
    assert list(search_eligible("   ")) == []


def test_the_search_never_offers_somebody_the_register_would_refuse(register):
    """A shop and its shopkeeper's household are both in the export."""
    assert list(search_eligible("kaffebaren")) == []
    assert list(search_eligible("iversen")) == []
