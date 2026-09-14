"""
Uploading the register through the admin, which is how the board actually does
it — the people who fetch the export from INNA are not the people with a shell
on the server.

Two things are worth guarding. The page must be reachable only by someone
granted the import permission, since an upload decides who the portal lets in
without asking anybody. And the file must be read whatever a board member's
spreadsheet did to it on the way, because "export it again, but do not open it
in Excel first" is not a thing to have to tell somebody twice a year.
"""

import csv
import io
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.csvsource import RegisterFileError, read_bytes
from apps.accounts.models import RegisterEntry, SignupRequest, SignupRequestStatus

User = get_user_model()

SAMPLE = Path(__file__).parent / "data" / "register_sample.csv"
IMPORT_URL = "/admin/accounts/registerentry/import/"


def sample_bytes(encoding="utf-8", delimiter=","):
    """The sample export, re-encoded the way a spreadsheet might have left it."""
    rows = list(csv.reader(SAMPLE.open(newline="", encoding="utf-8-sig")))
    out = io.StringIO(newline="")
    csv.writer(out, delimiter=delimiter, lineterminator="\r\n").writerows(rows)
    return out.getvalue().encode(encoding)


def upload(client, data=None, *, dry_run=False, filename="users.csv"):
    payload = {"file": SimpleUploadedFile(filename, data if data is not None else sample_bytes())}
    if dry_run:
        payload["dry_run"] = "on"
    return client.post(IMPORT_URL, payload, follow=True)


@pytest.fixture
def importer(db, client):
    """A board member with the import permission and nothing else unusual."""
    user = User.objects.create_user(
        email="bestyrelse@example.dk", password="hemmeligt123", is_staff=True
    )
    user.user_permissions.add(
        Permission.objects.get(codename="import_register"),
        Permission.objects.get(codename="view_registerentry"),
    )
    client.force_login(user)
    return client


# --------------------------------------------------------------------------
# Reading whatever file arrives
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("encoding", "delimiter", "why"),
    [
        ("utf-8", ",", "what INNA exports"),
        ("cp1252", ";", "what Excel makes of it on a Danish machine"),
        ("utf-8", ";", "semicolons but still UTF-8"),
        ("cp1252", ",", "Windows encoding, commas kept"),
        ("utf-8", "\t", "pasted through something tab-separated"),
    ],
)
def test_the_file_is_read_whatever_shape_it_arrives_in(encoding, delimiter, why):
    """Detected rather than asked for: the person uploading should not have to know."""
    header, rows = read_bytes(sample_bytes(encoding, delimiter))

    assert "Beboernr." in header, why
    assert len(rows) == 14, why


def test_a_byte_order_mark_does_not_swallow_the_first_column():
    """`utf-8-sig`, because a BOM would otherwise stop `Fornavn` matching."""
    header, _rows = read_bytes("﻿".encode() + sample_bytes())
    assert header[0] == "Fornavn"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", "tom"),
        (b"Navn,Email\nAne Bang,ane@example.dk\n", "mangler de kolonner"),
        (b"\x00\x01\x02 not a csv at all", "mangler de kolonner"),
    ],
)
def test_a_file_that_is_not_the_register_is_refused_in_danish(data, expected):
    with pytest.raises(RegisterFileError, match=expected):
        read_bytes(data)


def test_an_enormous_file_is_refused_before_anything_tries_to_parse_it():
    """The limit keeps an upload in memory, so no copy is ever written to disk."""
    with pytest.raises(RegisterFileError, match="næppe den rigtige fil"):
        read_bytes(b"x" * (3 * 1024 * 1024))


# --------------------------------------------------------------------------
# Who may use the page
# --------------------------------------------------------------------------


def test_an_anonymous_visitor_is_sent_to_the_login_page(client, db):
    response = client.get(IMPORT_URL)
    assert response.status_code == 302
    assert "/admin/login/" in response["Location"]


def test_a_staff_member_without_the_permission_is_refused(db, client):
    """Seeing the register is not the same as being able to replace it."""
    user = User.objects.create_user(email="kigger@example.dk", password="x", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_registerentry"))
    client.force_login(user)

    assert client.get(IMPORT_URL).status_code == 403
    assert upload(client).status_code == 403
    assert RegisterEntry.objects.count() == 0


def test_the_link_is_hidden_from_somebody_who_may_not_import(db, client):
    user = User.objects.create_user(email="kigger@example.dk", password="x", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="view_registerentry"))
    client.force_login(user)

    page = client.get(reverse("admin:accounts_registerentry_changelist")).content.decode()
    assert IMPORT_URL not in page


def test_the_link_is_shown_to_somebody_who_may(importer):
    page = importer.get(reverse("admin:accounts_registerentry_changelist")).content.decode()
    assert IMPORT_URL in page


# --------------------------------------------------------------------------
# Uploading
# --------------------------------------------------------------------------


def test_a_dry_run_reports_without_writing(importer):
    page = upload(importer, dry_run=True)

    assert RegisterEntry.objects.count() == 0
    body = page.content.decode()
    assert "Prøvekørsel" in body
    assert "Intet er gemt" in body


def test_an_upload_imports_and_reports_on_the_changelist(importer):
    page = upload(importer)

    assert RegisterEntry.objects.count() == 12
    assert RegisterEntry.objects.eligible().count() == 8
    body = page.content.decode()
    assert "12 nye" in body
    assert "8 kan aktivere en konto" in body


def test_an_upload_lands_back_on_the_register_so_a_refresh_cannot_repeat_it(importer):
    response = upload(importer)
    assert response.redirect_chain[-1][0].endswith("/admin/accounts/registerentry/")


def test_uploading_the_same_file_twice_changes_nothing(importer):
    upload(importer)
    page = upload(importer)

    assert RegisterEntry.objects.count() == 12
    assert "12 uændrede" in page.content.decode()


def test_an_upload_approves_the_signups_the_register_now_vouches_for(importer, client):
    """The whole point of doing this after a month of nobody importing."""
    applicant = User.objects.create_user(email="ny@example.dk", password="x", is_active=False)
    request = SignupRequest.objects.create(
        email=applicant.email,
        user=applicant,
        claimed_address="Jægersborggade 5, 1. tv.",
        claimed_resident_number="1-1121-4203-2",
    )

    page = upload(importer)

    request.refresh_from_db()
    applicant.refresh_from_db()
    assert request.status == SignupRequestStatus.APPROVED
    assert applicant.is_active is True
    assert applicant.resident.address == "Jægersborggade 5, 1. tv"
    assert "godkendt automatisk" in page.content.decode()


def test_a_wrong_file_comes_back_as_a_field_error_and_writes_nothing(importer):
    page = upload(importer, b"Navn,Email\nAne Bang,ane@example.dk\n")

    assert page.status_code == 200
    assert RegisterEntry.objects.count() == 0
    assert "mangler de kolonner" in page.content.decode()


def test_the_warnings_survive_the_redirect(importer):
    """A storage room and a caretaker are in every real export, and both warn."""
    body = upload(importer).content.decode()

    assert "kunne ikke læses som en bolig" in body
    assert "intet brugbart bolignr" in body
