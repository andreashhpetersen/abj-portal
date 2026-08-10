from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    """The member representation the SPA works with."""

    is_business_committee = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "apartment",
            "is_staff",
            "is_business_committee",
        ]
        read_only_fields = ["id", "email", "is_staff", "is_business_committee"]


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(style={"input_type": "password"}, trim_whitespace=False)
