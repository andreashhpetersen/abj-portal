"""
Who gets emailed around signup, and who must not be.

Two properties matter here. A request the register could not resolve must
reach the board without anyone clicking anything to create it — that email is
the only thing standing between a pending request and nobody noticing it
exists. And an auto-approved signup must not also trigger the applicant email
that a board approval sends: the page it just submitted already told it.
"""

import csv
from pathlib import Path

import pytest
from django.contrib import admin as django_admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory
from django.urls import reverse

from apps.accounts.admin import SignupRequestAdmin
from apps.accounts.models import SignupRequest, User
from apps.accounts.register import import_rows, parse_rows

SAMPLE = Path(__file__).parent / "data" / "register_sample.csv"

VALID = {
    "email": "ny.beboer@example.dk",
    "first_name": "Ny",
    "last_name": "Beboer",
    "phone": "12345678",
    "address": "Sankt Knuds Vej 12, 3. th",
    "resident_number": "1-2345-6789-0",
    "password": "vinter-cykel-lampe-42",
}

#: Resolves against `register_sample.csv` to one eligible unit — see
#: test_register.py, which uses the same pair for the same reason.
MATCHED = {**VALID, "address": "Jægersborggade 5, 1. tv.", "resident_number": "1-1121-4203-2"}


@pytest.fixture(autouse=True)
def clear_throttle_state():
    """Throttle history lives in the cache, which outlives a single test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def register(db):
    """The sample register, imported — enough for one signup to auto-approve."""
    with SAMPLE.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    header, data_rows = rows[0], rows[1:]
    return import_rows(*parse_rows(header, data_rows))


@pytest.fixture
def board_member(db):
    return User.objects.create_user(email="bestyrelse@example.dk", password="hemmeligt123")


def signup(client, data=VALID, **overrides):
    return client.post(
        reverse("accounts:signup"), {**data, **overrides}, content_type="application/json"
    )


def approve_via_admin(queryset, by):
    """Drives the real admin action, messages framework and all.

    Not a call to `SignupRequest.approve()` directly: the applicant email is
    wired into `SignupRequestAdmin._apply`, not the model, precisely so an
    auto-approved signup — which also calls `approve()`, from
    `register.auto_approve` — does not pick it up. Exercising anything less
    than the actual action would not prove that separation holds.
    """
    request = RequestFactory().post("/admin/accounts/signuprequest/")
    request.user = by
    request.session = "session"
    request._messages = FallbackStorage(request)
    SignupRequestAdmin(SignupRequest, django_admin.site).approve_selected(request, queryset)


def reject_via_admin(queryset, by):
    request = RequestFactory().post("/admin/accounts/signuprequest/")
    request.user = by
    request.session = "session"
    request._messages = FallbackStorage(request)
    SignupRequestAdmin(SignupRequest, django_admin.site).reject_selected(request, queryset)


def test_a_pending_signup_notifies_the_board(client, db, settings):
    settings.SIGNUP_NOTIFICATION_EMAIL = "bestyrelse@example.dk"

    response = signup(client)

    assert response.status_code == 202
    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == ["bestyrelse@example.dk"]
    assert VALID["email"] in sent.body
    assert VALID["address"] in sent.body


def test_nothing_is_sent_when_no_board_address_is_configured(client, db, settings):
    """A bare checkout must still be able to run signup with no board mailbox set."""
    settings.SIGNUP_NOTIFICATION_EMAIL = ""

    signup(client)

    assert len(mail.outbox) == 0


def test_an_auto_approved_signup_does_not_notify_the_board(client, register, settings):
    settings.SIGNUP_NOTIFICATION_EMAIL = "bestyrelse@example.dk"

    signup(client, data=MATCHED)

    assert User.objects.get(email=MATCHED["email"]).is_active is True
    assert len(mail.outbox) == 0


def test_approving_a_pending_request_emails_the_applicant(client, board_member):
    signup(client)
    pending = SignupRequest.objects.get()

    approve_via_admin(SignupRequest.objects.filter(pk=pending.pk), by=board_member)

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == [VALID["email"]]
    assert "/login" in sent.body


def test_rejecting_a_pending_request_emails_nobody(client, board_member):
    signup(client)
    pending = SignupRequest.objects.get()

    reject_via_admin(SignupRequest.objects.filter(pk=pending.pk), by=board_member)

    assert len(mail.outbox) == 0


def test_an_auto_approved_signup_never_reaches_the_admin_action(client, register):
    """Nothing to approve — proves the applicant email genuinely has one path."""
    signup(client, data=MATCHED)

    approve_via_admin(
        SignupRequest.objects.all(),
        by=User.objects.create_user(email="admin2@example.dk", password="whatever-1"),
    )

    assert len(mail.outbox) == 0
