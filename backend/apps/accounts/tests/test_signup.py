"""
Covers public signup and the board approval that stands between it and a login.

The two properties worth guarding here are that nothing created by signup can
log in before a human has looked at it, and that the endpoint answers the same
way to everyone — a signup form that distinguishes "created" from "already
exists" is a way to ask the portal who lives here.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.models import Building, Resident, SignupRequest, SignupRequestStatus
from apps.accounts.views import SIGNUP_RECEIVED

User = get_user_model()

VALID = {
    "email": "ny.beboer@example.dk",
    "first_name": "Ny",
    "last_name": "Beboer",
    "phone": "12345678",
    "address": "Sankt Knuds Vej 12, 3. th",
    "resident_number": "1-2345-6789-0",
    "password": "vinter-cykel-lampe-42",
}


@pytest.fixture(autouse=True)
def clear_throttle_state():
    """Throttle history lives in the cache, which outlives a single test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def board_member(db):
    return User.objects.create_user(email="bestyrelse@example.dk", password="hemmeligt123")


def signup(client, **overrides):
    return client.post(
        reverse("accounts:signup"),
        {**VALID, **overrides},
        content_type="application/json",
    )


def login(client, email=VALID["email"], password=VALID["password"]):
    return client.post(
        reverse("accounts:login"),
        {"email": email, "password": password},
        content_type="application/json",
    )


def test_signup_creates_an_inactive_account_and_a_pending_request(client, db):
    response = signup(client)

    assert response.status_code == 202
    user = User.objects.get(email=VALID["email"])
    assert user.is_active is False
    assert user.get_full_name() == "Ny Beboer"
    assert user.phone == "12345678"

    request = SignupRequest.objects.get()
    assert request.user == user
    assert request.email == VALID["email"]
    assert request.status == SignupRequestStatus.PENDING
    assert request.claimed_address == "Sankt Knuds Vej 12, 3. th"
    assert request.claimed_resident_number == "1-2345-6789-0"


def test_signup_stores_the_address_as_a_claim_and_not_as_residency(client, db):
    """The claim is the board's to check; `Resident` belongs to the other database."""
    signup(client)

    user = User.objects.get(email=VALID["email"])
    assert user.is_resident is False
    assert Resident.objects.count() == 0
    assert Building.objects.count() == 0


def test_signup_needs_no_login_and_no_phone_number(client, db):
    """Phone is the one optional field: it is a convenience, not a claim."""
    response = signup(client, phone="")

    assert response.status_code == 202
    assert SignupRequest.objects.get().user.phone == ""


def test_signup_without_a_resident_number_is_rejected(client, db):
    """The form is for residents, and the number is how the board finds them.

    A field error is the one thing signup answers other than the uniform 202,
    and it is safe to: it describes what the submitter typed, not who already
    has an account here.
    """
    response = signup(client, resident_number="")

    assert response.status_code == 400
    assert "beboernummer" in response.json()["resident_number"][0]
    assert User.objects.count() == 0
    assert SignupRequest.objects.count() == 0


def test_a_pending_account_cannot_log_in(client, db):
    signup(client)

    response = login(client)

    assert response.status_code == 403
    assert "_auth_user_id" not in client.session


def test_a_pending_account_is_told_why_when_the_password_is_right(client, db):
    signup(client)

    response = login(client)

    assert response.json()["detail"] == "Din konto afventer godkendelse fra bestyrelsen."


def test_a_wrong_password_on_a_pending_account_stays_vague(client, db):
    """Naming the reason is safe only for someone who proved the password."""
    signup(client)

    response = login(client, password="forkert")

    assert response.status_code == 401
    assert response.json()["detail"] == "Forkert email eller adgangskode."


def test_approval_activates_the_account_and_records_who_did_it(client, board_member):
    signup(client)
    request = SignupRequest.objects.get()

    request.approve(by=board_member)

    request.refresh_from_db()
    assert request.status == SignupRequestStatus.APPROVED
    assert request.reviewed_by == board_member
    assert request.status_changed_at is not None
    assert User.objects.get(email=VALID["email"]).is_active is True


def test_an_approved_user_can_log_in(client, board_member):
    signup(client)
    SignupRequest.objects.get().approve(by=board_member)

    response = login(client)

    assert response.status_code == 200
    assert response.json()["email"] == VALID["email"]
    assert response.json()["resident"] is None


def test_signup_with_a_known_email_creates_nothing_and_looks_identical(client, db):
    first = signup(client)
    assert User.objects.filter(email=VALID["email"]).count() == 1

    second = signup(client, first_name="Anden", address="Et helt andet sted 1")

    assert (second.status_code, second.json()) == (first.status_code, first.json())
    assert User.objects.filter(email=VALID["email"]).count() == 1
    assert SignupRequest.objects.count() == 1
    assert SignupRequest.objects.get().claimed_address == VALID["address"]


def test_an_existing_email_in_another_case_is_still_taken(client, db):
    signup(client)

    signup(client, email="Ny.Beboer@example.dk")

    assert SignupRequest.objects.count() == 1


def test_a_filled_honeypot_looks_identical_and_creates_nothing(client, db):
    response = signup(client, website="http://spam.example")

    assert response.status_code == 202
    assert response.json()["detail"] == str(SIGNUP_RECEIVED)
    assert User.objects.count() == 0
    assert SignupRequest.objects.count() == 0


def test_rejection_deletes_the_account_so_the_email_is_free_again(client, board_member):
    """Otherwise anyone could reserve a resident's address before they register."""
    signup(client)
    SignupRequest.objects.get().reject(by=board_member, note="Bor ikke her.")

    assert User.objects.filter(email=VALID["email"]).exists() is False

    signup(client)

    assert User.objects.filter(email=VALID["email"]).count() == 1
    assert SignupRequest.objects.filter(status=SignupRequestStatus.PENDING).count() == 1


def test_rejection_keeps_a_record_of_what_was_claimed(client, board_member):
    signup(client)
    request = SignupRequest.objects.get()

    request.reject(by=board_member, note="Bor ikke her.")

    request.refresh_from_db()
    assert request.status == SignupRequestStatus.REJECTED
    assert request.user is None
    assert request.email == VALID["email"]
    assert request.claimed_address == VALID["address"]
    assert request.review_note == "Bor ikke her."
    assert request.reviewed_by == board_member


def test_a_decided_request_cannot_be_decided_again(client, board_member):
    signup(client)
    request = SignupRequest.objects.get()
    request.approve(by=board_member)

    with pytest.raises(ValueError):
        request.approve(by=board_member)
    with pytest.raises(ValueError):
        request.reject(by=board_member)


def test_a_weak_password_is_rejected(client, db):
    response = signup(client, password="1234")

    assert response.status_code == 400
    assert "password" in response.json()
    assert User.objects.count() == 0


def test_a_password_resembling_the_email_is_rejected(client, db):
    response = signup(client, password="ny.beboer@example.dk")

    assert response.status_code == 400
    assert User.objects.count() == 0


def test_repeated_signups_from_one_address_are_throttled(client, db, monkeypatch):
    """Two through, third refused — the number itself is a setting, not a rule.

    Patching the dict rather than overriding `settings.REST_FRAMEWORK`:
    `SimpleRateThrottle.THROTTLE_RATES` is bound to the rates dict once, when
    the class is defined, so reassigning the setting leaves the throttle reading
    the old object and the test passes while testing nothing.
    """
    monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, "signup", "2/hour")

    assert signup(client, email="en@example.dk").status_code == 202
    assert signup(client, email="to@example.dk").status_code == 202
    blocked = signup(client, email="tre@example.dk")

    assert blocked.status_code == 429
    assert User.objects.filter(email="tre@example.dk").exists() is False
