"""
The member account model.

Members are identified by email rather than a username: the association knows
its residents by email, and the booking calendar shows an email as the contact
detail for whoever booked a slot.
"""

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _

#: Members of this Django group may access the shop-rental applications.
#: Danish for "business committee" — kept in Danish to match how the
#: association refers to itself in its own bylaws.
ERHVERVSUDVALG_GROUP = "erhvervsudvalg"


class UserManager(BaseUserManager):
    """User manager keyed on email instead of username."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """A resident of the housing cooperative."""

    username = None  # replaced by email
    email = models.EmailField(_("email address"), unique=True)
    phone = models.CharField(
        _("phone number"),
        max_length=32,
        blank=True,
        help_text=_("Optional. Shown as contact info on bookings when provided."),
    )
    apartment = models.CharField(
        _("apartment"),
        max_length=64,
        blank=True,
        help_text=_("Free text, e.g. '3. th'."),
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = _("member")
        verbose_name_plural = _("members")
        ordering = ["email"]

    def __str__(self):
        return self.get_full_name() or self.email

    @property
    def is_business_committee(self):
        """Whether this member may see shop-rental applications."""
        return self.is_superuser or self.groups.filter(name=ERHVERVSUDVALG_GROUP).exists()
