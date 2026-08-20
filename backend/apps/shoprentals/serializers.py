"""
Serializers for the shop-rental API.

The governing rule is the same split the models are built around: the applicant
owns their answers, the committee owns everything else. So every form-supplied
field is read-only here. The API can no more edit an applicant's answers than
the sync can edit the committee's rating — and if a name needs correcting, that
is a correction to the contract details, not a rewrite of what was submitted.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.accounts.serializers import ContactSerializer

from .models import Application, ApplicationComment, ApplicationDetails

User = get_user_model()


class CommitteeMemberSerializer(serializers.ModelSerializer):
    """A committee member, as the assignee dropdown needs them."""

    name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "name", "email"]
        read_only_fields = fields

    def get_name(self, obj) -> str:
        # Full name when we have one, email otherwise — a dropdown of blanks is
        # useless, and not every committee member has filled in their name.
        return obj.get_full_name() or obj.email


class ApplicationCommentSerializer(serializers.ModelSerializer):
    author = ContactSerializer(read_only=True)
    can_delete = serializers.SerializerMethodField()

    class Meta:
        model = ApplicationComment
        fields = ["id", "body", "author", "created_at", "can_delete"]
        read_only_fields = ["id", "author", "created_at", "can_delete"]

    def get_can_delete(self, obj) -> bool:
        """Your own comments, or anything if you are an admin. The server
        decides; the UI only reflects it."""
        user = self.context["request"].user
        return obj.author_id == user.pk or user.is_staff

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError("Kommentaren er tom.")
        return value.strip()


class ApplicationDetailsSerializer(serializers.ModelSerializer):
    """The facts gathered after making contact — all optional, always.

    A half-filled record is the normal state, so nothing here is required. What
    the serializer adds is `missing_fields`: the labels of what the lawyer still
    needs, so the UI can show a checklist rather than the committee guessing.
    """

    missing_fields = serializers.ListField(child=serializers.CharField(), read_only=True)
    is_complete = serializers.BooleanField(read_only=True)

    class Meta:
        model = ApplicationDetails
        fields = [
            "company_name",
            "cvr",
            "legal_form",
            "company_address",
            "contact_name",
            "contact_email",
            "contact_phone",
            "unit_label",
            "purpose",
            "area_sqm",
            "annual_rent_dkk",
            "deposit_months",
            "lease_start",
            "notes",
            "updated_at",
            "missing_fields",
            "is_complete",
        ]
        read_only_fields = ["updated_at", "missing_fields", "is_complete"]

    def validate(self, attrs):
        # Model-level validators (the CVR format) do not run through DRF's
        # ModelSerializer on their own, and a bad CVR reaching the database
        # would surface as a 500 rather than a field error.
        candidate = self.instance or ApplicationDetails()
        for field, value in attrs.items():
            setattr(candidate, field, value)
        try:
            candidate.clean_fields(exclude=["application"])
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
        return attrs


class ApplicationSerializer(serializers.ModelSerializer):
    """One application, list and detail alike.

    Writable: status, rating, assignee. That is the whole of what the committee
    changes on an application itself — comments and contract details have their
    own endpoints, and everything the applicant wrote is read-only.
    """

    assignee = CommitteeMemberSerializer(read_only=True)
    assignee_id = serializers.PrimaryKeyRelatedField(
        source="assignee",
        queryset=User.objects.all(),
        write_only=True,
        required=False,
        allow_null=True,
        # Danish, because it is shown to the committee.
        error_messages={"does_not_exist": "Brugeren findes ikke."},
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    comment_count = serializers.SerializerMethodField()
    has_details = serializers.SerializerMethodField()

    class Meta:
        model = Application
        fields = [
            "id",
            "submitted_at",
            "applicant_name",
            "email",
            "phone",
            "answers",
            "status",
            "status_display",
            "status_changed_at",
            "rating",
            "assignee",
            "assignee_id",
            "comment_count",
            "has_details",
            "synced_at",
        ]
        read_only_fields = [
            "id",
            "submitted_at",
            "applicant_name",
            "email",
            "phone",
            "answers",
            "status_changed_at",
            "synced_at",
        ]

    def get_comment_count(self, obj) -> int:
        annotated = getattr(obj, "_comment_count", None)
        return annotated if annotated is not None else obj.comments.count()

    def get_has_details(self, obj) -> bool:
        """Whether anyone has started filling in the contract details."""
        return hasattr(obj, "details")

    def validate_assignee_id(self, value):
        """Only the erhvervsudvalg can be made responsible for an application.

        Named for the serializer field rather than the model field it writes to:
        DRF looks up `validate_<field_name>`, so calling this `validate_assignee`
        would define a method nothing ever calls.

        Assigning someone outside the committee would hand them a task on a page
        they cannot open.
        """
        if value is None:
            return value
        if not value.is_business_committee:
            raise serializers.ValidationError(
                "Kun medlemmer af erhvervsudvalget kan gøres ansvarlige."
            )
        return value

    def validate_rating(self, value):
        # Mirrors the model validators, so a bad rating is a 400 with a Danish
        # message rather than a 500 from the database.
        if value is not None and not 1 <= value <= 5:
            raise serializers.ValidationError("Vurderingen skal være mellem 1 og 5.")
        return value

    def update(self, instance, validated_data):
        # Route status through set_status so status_changed_at is stamped —
        # assigning the field directly would leave "I gang siden marts"
        # permanently blank, which is the one thing it exists to answer.
        status = validated_data.pop("status", None)
        if status is not None:
            instance.set_status(status, save=False)
        return super().update(instance, validated_data)
