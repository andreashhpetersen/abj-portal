"""Covers the session-auth flow the SPA depends on, plus committee gating."""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.accounts.models import ERHVERVSUDVALG_GROUP

User = get_user_model()


@pytest.fixture
def member(db):
    return User.objects.create_user(email="beboer@example.dk", password="hemmeligt123")


def test_user_is_created_with_email_as_identifier(member):
    assert member.email == "beboer@example.dk"
    assert member.check_password("hemmeligt123")
    assert member.get_username() == "beboer@example.dk"


def test_login_returns_the_user_and_starts_a_session(client, member):
    response = client.post(
        reverse("accounts:login"),
        {"email": "beboer@example.dk", "password": "hemmeligt123"},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["email"] == "beboer@example.dk"
    assert client.session.get("_auth_user_id") == str(member.pk)


def test_login_with_a_wrong_password_is_rejected(client, member):
    response = client.post(
        reverse("accounts:login"),
        {"email": "beboer@example.dk", "password": "forkert"},
        content_type="application/json",
    )
    assert response.status_code == 401
    assert "_auth_user_id" not in client.session


def test_me_requires_authentication(client, db):
    assert client.get(reverse("accounts:me")).status_code == 403


def test_me_returns_the_logged_in_user(client, member):
    client.force_login(member)
    response = client.get(reverse("accounts:me"))
    assert response.status_code == 200
    assert response.json()["email"] == "beboer@example.dk"


def test_plain_user_is_not_on_the_business_committee(member):
    assert member.is_business_committee is False


def test_group_membership_grants_business_committee_access(member):
    member.groups.add(Group.objects.create(name=ERHVERVSUDVALG_GROUP))
    assert member.is_business_committee is True
