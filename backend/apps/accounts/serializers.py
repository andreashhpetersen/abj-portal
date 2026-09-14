from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from .models import Building, Resident, SignupRequest, User


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


class SignupSerializer(serializers.Serializer):
    """The public signup form.

    A plain `Serializer` rather than a `ModelSerializer` on `User`, on purpose:
    a ModelSerializer would pick up the unique constraint on `email` and answer
    an unknown visitor with "der findes allerede en bruger med denne email" —
    turning the signup form into a way to ask the portal who lives here. The
    duplicate is handled in the view instead, by accepting the request and
    quietly doing nothing.

    **This form is for residents, so `resident_number` is required** — the only
    optional field is `phone`. It is still not format-validated: the board reads
    it, and a mistyped digit next to a name and an address costs them nothing,
    whereas a rejected signup teaches the applicant nothing. Requiring it is
    about the board being able to find the person at all; an address alone can
    match a flat with two names on the door, and the number is what resolves
    that in one lookup rather than a phone call.

    Employees and third-party managers therefore do not sign up here. They have
    no resident number to give, and they are few enough and known enough that
    the board creates their accounts in the admin — which is also the only place
    that can grant them anything beyond a login.

    `website` is a honeypot. It is not a real field, no template renders it
    visibly, and a human never fills it in; a form-filling bot fills everything
    it finds. Checking it costs one comparison and removes the entire
    naive-automation class without a third-party CAPTCHA — which this project
    cannot easily have anyway, since reCAPTCHA is Google's and Turnstile is
    Cloudflare's, and INFRASTRUCTURE.md rules on European ownership rather than
    mere data residency.
    """

    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    address = serializers.CharField(max_length=255)
    resident_number = serializers.CharField(
        max_length=32,
        error_messages={
            "blank": "Skriv dit beboernummer. Det står på din huslejeopkrævning.",
            "required": "Skriv dit beboernummer. Det står på din huslejeopkrævning.",
        },
    )
    password = serializers.CharField(style={"input_type": "password"}, trim_whitespace=False)
    website = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_password(self, value):
        """Run Django's configured password validators.

        Handed an unsaved `User` so `UserAttributeSimilarityValidator` can do its
        job — without it, "beboer@example.dk" would be an acceptable password for
        beboer@example.dk. The messages come back translated, because the project
        runs with `LANGUAGE_CODE = "da-dk"` and Django ships Danish for these.
        """
        candidate = User(
            email=self.initial_data.get("email") or "",
            first_name=self.initial_data.get("first_name") or "",
            last_name=self.initial_data.get("last_name") or "",
        )
        try:
            validate_password(value, user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value

    @transaction.atomic
    def create(self, validated_data):
        """Create the provisional account and the request that has to clear it.

        The account is inactive, so `ModelBackend` refuses it and `LoginView`
        needs no special case. One transaction, because an account with no
        request would be invisible to the board and never get approved.
        """
        user = User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            phone=validated_data["phone"],
            is_active=False,
        )
        return SignupRequest.objects.create(
            email=user.email,
            user=user,
            claimed_address=validated_data["address"],
            claimed_resident_number=validated_data["resident_number"],
        )
