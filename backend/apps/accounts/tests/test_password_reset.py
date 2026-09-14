"""
Covers "forgot your password" end to end.

The property worth guarding on the request side is the same as signup's: the
response must not say whether the address has an account. On the confirm side
it is that a link only works once, only for the account it was issued to, and
only until that account's password changes again.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.views import PASSWORD_RESET_SENT

User = get_user_model()


@pytest.fixture(autouse=True)
def clear_throttle_state():
    """Throttle history lives in the cache, which outlives a single test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def member(db):
    return User.objects.create_user(email="beboer@example.dk", password="det-gamle-kodeord")


@pytest.fixture
def pending(db):
    return User.objects.create_user(
        email="ansoeger@example.dk", password="hvad-som-helst", is_active=False
    )


def request_reset(client, email):
    return client.post(
        reverse("accounts:password-reset"),
        {"email": email},
        content_type="application/json",
    )


def link_for(user):
    """The (uid, token) pair the email would have sent, decoded for a test."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return uid, token


def confirm_reset(client, uid, token, new_password):
    return client.post(
        reverse("accounts:password-reset-confirm"),
        {"uid": uid, "token": token, "new_password": new_password},
        content_type="application/json",
    )


def test_request_answers_the_same_receipt_for_a_known_and_an_unknown_address(client, member):
    known = request_reset(client, member.email)
    unknown = request_reset(client, "niemand@example.dk")

    assert known.status_code == 202
    assert unknown.status_code == 202
    assert known.json()["detail"] == unknown.json()["detail"] == PASSWORD_RESET_SENT


def test_request_sends_a_link_to_a_known_active_user(client, member):
    response = request_reset(client, member.email)

    assert response.status_code == 202
    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == [member.email]
    assert "/reset-password/" in sent.body


def test_request_sends_nothing_for_an_unknown_address(client, db):
    request_reset(client, "niemand@example.dk")

    assert len(mail.outbox) == 0


def test_request_sends_nothing_for_an_account_still_awaiting_board_approval(client, pending):
    """An inactive account has no working password to reset into yet, and a
    link would confirm to an unapproved applicant that their account exists."""
    request_reset(client, pending.email)

    assert len(mail.outbox) == 0


def test_repeated_password_reset_requests_are_throttled(client, db, monkeypatch):
    monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, "password_reset", "2/hour")

    assert request_reset(client, "en@example.dk").status_code == 202
    assert request_reset(client, "to@example.dk").status_code == 202
    assert request_reset(client, "tre@example.dk").status_code == 429


def test_confirm_sets_the_new_password_and_logs_in_with_it(client, member):
    uid, token = link_for(member)

    response = confirm_reset(client, uid, token, "det-nye-kodeord-42")

    assert response.status_code == 204
    member.refresh_from_db()
    assert member.check_password("det-nye-kodeord-42")


def test_confirm_rejects_a_token_for_the_wrong_account(client, member):
    other = User.objects.create_user(email="anden@example.dk", password="whatever-1")
    uid = link_for(member)[0]
    other_token = link_for(other)[1]

    response = confirm_reset(client, uid, other_token, "det-nye-kodeord-42")

    assert response.status_code == 400
    assert response.json()["detail"]
    member.refresh_from_db()
    assert member.check_password("det-gamle-kodeord")


def test_confirm_rejects_an_unknown_uid(client, db):
    bogus_uid = urlsafe_base64_encode(force_bytes(999999))

    response = confirm_reset(client, bogus_uid, "irrelevant-token", "det-nye-kodeord-42")

    assert response.status_code == 400


def test_confirm_rejects_a_link_already_used_once(client, member):
    """Using it changes the password hash the token is derived from, so a
    second use of the same link — someone else finding the email, a resident
    clicking it twice — fails rather than reopening the account."""
    uid, token = link_for(member)
    confirm_reset(client, uid, token, "det-nye-kodeord-42")

    replay = confirm_reset(client, uid, token, "et-tredje-kodeord")

    assert replay.status_code == 400
    member.refresh_from_db()
    assert member.check_password("det-nye-kodeord-42")


def test_confirm_rejects_an_account_pending_board_approval(client, pending):
    uid, token = link_for(pending)

    response = confirm_reset(client, uid, token, "det-nye-kodeord-42")

    assert response.status_code == 400


def test_confirm_enforces_the_configured_password_validators(client, member):
    uid, token = link_for(member)

    response = confirm_reset(client, uid, token, "1234")

    assert response.status_code == 400
    assert "new_password" in response.json()
    member.refresh_from_db()
    assert member.check_password("det-gamle-kodeord")
