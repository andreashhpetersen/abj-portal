from rest_framework import serializers

from .models import Building, Resident, User


class ContactSerializer(serializers.ModelSerializer):
    """How to reach someone — what the calendar shows next to a booking.

    Phone is included because the brief asks for it as an optional contact
    detail; it is blank for anyone who has not supplied one.
    """

    name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = User
        fields = ["id", "name", "email", "phone"]
        read_only_fields = fields


class BuildingSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="__str__", read_only=True)

    class Meta:
        model = Building
        fields = ["id", "street", "house_number", "name", "label"]


class ResidentSerializer(serializers.ModelSerializer):
    """Residency as the SPA sees it. Read-only: the other database owns this."""

    building = BuildingSerializer(read_only=True)
    address = serializers.CharField(read_only=True)

    class Meta:
        model = Resident
        fields = ["external_user_id", "resident_number", "building", "floor", "door", "address"]
        read_only_fields = fields


class UserSerializer(serializers.ModelSerializer):
    """The user representation the SPA works with.

    `resident` is null for employees and third-party managers, so the frontend
    must treat it as optional rather than assuming an address exists.
    """

    # A method field rather than a nested serializer: for a user with no
    # Resident row, DRF would drop the key entirely, and the frontend is easier
    # to write against a key that is always present and sometimes null.
    resident = serializers.SerializerMethodField()
    is_business_committee = serializers.BooleanField(read_only=True)

    def get_resident(self, obj) -> dict | None:
        return ResidentSerializer(obj.resident).data if obj.is_resident else None

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "is_staff",
            "is_business_committee",
            "resident",
        ]
        read_only_fields = ["id", "email", "is_staff", "is_business_committee", "resident"]


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(style={"input_type": "password"}, trim_whitespace=False)
