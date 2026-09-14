"""Residency: who is a resident, what the identity numbers may look like, and
what the API exposes for users who are not residents at all.

The numbers are the part worth reading carefully. `unit_number` is the flat and
`resident_number` the tenancy, and the tenancy is shared by everyone living in
the flat — so the constraint most naturally reached for here, uniqueness, would
lock a couple's second login out. See `apps/accounts/register.py` for where
they come from."""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import Building, Resident

User = get_user_model()


@pytest.fixture
def building(db):
    return Building.objects.create(street="Sankt Knuds Vej", house_number="12")


@pytest.fixture
def resident(building):
    user = User.objects.create_user(email="beboer@example.dk", password="hemmeligt123")
    return Resident.objects.create(
        user=user,
        unit_number="1-2345-6789",
        resident_number="1-2345-6789-0",
        building=building,
        floor="3",
        door="th",
    )


@pytest.fixture
def employee(db):
    """A user who can log in but does not live here — e.g. a manager."""
    return User.objects.create_user(email="vicevaert@example.dk", password="hemmeligt123")


def test_resident_user_is_marked_as_a_resident(resident):
    assert resident.user.is_resident is True


def test_non_resident_user_is_not_marked_as_a_resident(employee):
    assert employee.is_resident is False


def test_address_is_built_from_the_building_and_the_flat(resident):
    assert resident.address == "Sankt Knuds Vej 12, 3. th"


def test_address_omits_the_door_when_the_floor_is_a_single_flat(building):
    user = User.objects.create_user(email="stuen@example.dk", password="hemmeligt123")
    lone = Resident.objects.create(
        user=user,
        unit_number="1-2345-6790",
        resident_number="1-2345-6790-1",
        building=building,
        floor="st.",
        door="",
    )
    assert lone.address == "Sankt Knuds Vej 12, st."


@pytest.mark.parametrize(
    "number",
    [
        "1-2345-6789-0",
        "0-0000-0000-0",
        "1-1121-5007-10",  # a two-digit tenancy segment — the register has these
        "1-1121-409-2",  # and a three-digit middle group
    ],
)
def test_valid_resident_numbers_are_accepted(building, number):
    """The shapes the real register actually contains, not the tidy one.

    A validator pinned to `1-2345-6789-0` rejected two dozen real residents.
    """
    user = User.objects.create_user(email=f"{number}@example.dk", password="hemmeligt123")
    person = Resident(
        user=user,
        unit_number="1-2345-6789",
        resident_number=number,
        building=building,
        floor="1",
        door="tv",
    )
    person.full_clean()  # does not raise


@pytest.mark.parametrize(
    "number",
    [
        "12345678900",  # no separators
        "a-2345-6789-0",  # letters
        "1-2345-6789",  # a unit number, not a tenancy
        "1-2345-6789-0-0",  # one group too many
    ],
)
def test_malformed_resident_numbers_are_rejected(building, employee, number):
    person = Resident(
        user=employee,
        unit_number="1-2345-6789",
        resident_number=number,
        building=building,
        floor="1",
        door="tv",
    )
    with pytest.raises(ValidationError) as caught:
        person.full_clean()
    assert "resident_number" in caught.value.error_dict


def test_two_residents_can_share_one_flat(resident, building):
    """Spouses and adult family members each need their own login."""
    partner_user = User.objects.create_user(email="partner@example.dk", password="hemmeligt123")
    partner = Resident.objects.create(
        user=partner_user,
        unit_number=resident.unit_number,
        resident_number=resident.resident_number,
        building=building,
        floor=resident.floor,
        door=resident.door,
    )
    assert partner.address == resident.address


def test_the_two_people_in_a_flat_share_its_numbers(resident, building, employee):
    """Neither number identifies a person, and a unique constraint on either
    would mean the second person in a household could never get a login.

    This is not a detail of the model: it is what the register says. `Beboernr.`
    is the tenancy, `Bolignr.` the flat, and both belong to the home rather than
    to whoever lives in it.
    """
    flatmate = Resident.objects.create(
        user=employee,
        unit_number=resident.unit_number,
        resident_number=resident.resident_number,
        building=building,
        floor=resident.floor,
        door=resident.door,
    )
    assert flatmate.pk != resident.pk


def test_a_residency_finds_the_register_rows_describing_it(resident):
    """Looked up by unit, because a register row has no id of its own."""
    assert list(resident.register_entries) == []


def test_api_exposes_the_residency_of_a_resident(client, resident):
    client.force_login(resident.user)
    payload = client.get(reverse("accounts:me")).json()
    assert payload["resident"]["resident_number"] == "1-2345-6789-0"
    assert payload["resident"]["unit_number"] == "1-2345-6789"
    assert payload["resident"]["address"] == "Sankt Knuds Vej 12, 3. th"
    assert payload["resident"]["building"]["label"] == "Sankt Knuds Vej 12"


def test_api_reports_null_residency_for_a_non_resident(client, employee):
    """The key must be present and null, not missing — the SPA relies on it."""
    client.force_login(employee)
    payload = client.get(reverse("accounts:me")).json()
    assert "resident" in payload
    assert payload["resident"] is None
