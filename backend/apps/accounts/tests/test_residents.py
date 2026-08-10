"""Residency: who is a resident, what a resident number may look like, and
what the API exposes for users who are not residents at all."""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
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
        external_user_id=4711,
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
        external_user_id=4712,
        resident_number="1-2345-6789-1",
        building=building,
        floor="st.",
        door="",
    )
    assert lone.address == "Sankt Knuds Vej 12, st."


@pytest.mark.parametrize("number", ["1-2345-6789-0", "0-0000-0000-0"])
def test_valid_resident_numbers_are_accepted(building, number):
    user = User.objects.create_user(email=f"{number}@example.dk", password="hemmeligt123")
    person = Resident(
        user=user,
        external_user_id=hash(number) % 10**9,
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
        "1-234-6789-0",  # wrong grouping
        "a-2345-6789-0",  # letters
        "1-2345-6789",  # missing final digit
        "1-2345-6789-01",  # trailing digit too long
    ],
)
def test_malformed_resident_numbers_are_rejected(building, employee, number):
    person = Resident(
        user=employee,
        external_user_id=99,
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
        external_user_id=4713,
        resident_number="1-2345-6789-2",
        building=building,
        floor=resident.floor,
        door=resident.door,
    )
    assert partner.address == resident.address


def test_external_user_id_is_unique(resident, building, employee):
    with pytest.raises(IntegrityError):
        Resident.objects.create(
            user=employee,
            external_user_id=resident.external_user_id,
            resident_number="1-9999-9999-9",
            building=building,
            floor="2",
            door="tv",
        )


def test_api_exposes_the_residency_of_a_resident(client, resident):
    client.force_login(resident.user)
    payload = client.get(reverse("accounts:me")).json()
    assert payload["resident"]["resident_number"] == "1-2345-6789-0"
    assert payload["resident"]["external_user_id"] == 4711
    assert payload["resident"]["address"] == "Sankt Knuds Vej 12, 3. th"
    assert payload["resident"]["building"]["label"] == "Sankt Knuds Vej 12"


def test_api_reports_null_residency_for_a_non_resident(client, employee):
    """The key must be present and null, not missing — the SPA relies on it."""
    client.force_login(employee)
    payload = client.get(reverse("accounts:me")).json()
    assert "resident" in payload
    assert payload["resident"] is None
