"""
Accounts and residency.

Two things are deliberately kept apart:

* `User` is *anyone who can log in* — residents, but also employees and
  third-party managers who have no home in the association.
* `Resident` is the residency itself: which flat, which resident number, and
  the id of the same person in the association's other database.

Modelling it this way keeps the resident fields non-nullable (a Resident row
cannot exist half-filled) and means an employee account simply has no Resident
row rather than a spread of empty columns.
"""

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

#: Members of this Django group may access the shop-rental applications.
#: Danish for "business committee" — kept in Danish to match how the
#: association refers to itself in its own bylaws.
ERHVERVSUDVALG_GROUP = "erhvervsudvalg"

#: Resident numbers look like 1-2345-6789-0.
resident_number_validator = RegexValidator(
    regex=r"^\d-\d{4}-\d{4}-\d$",
    message=_("Beboernummer skal have formatet 1-2345-6789-0."),
)


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

    def business_committee(self):
        """Active users that `User.is_business_committee` is true for.

        The property's rule expressed once in SQL, so a view needing the list of
        committee members — to offer them as assignees, say — does not re-derive
        it by naming the group inline. Deactivated accounts are left out: they
        cannot log in, so offering them as an assignee only misleads.
        """
        return self.filter(
            models.Q(groups__name=ERHVERVSUDVALG_GROUP) | models.Q(is_superuser=True),
            is_active=True,
        ).distinct()


class User(AbstractUser):
    """Anyone who can log in.

    Residency lives on the related `Resident` row, which most users have and
    some — employees, external managers — do not.
    """

    username = None  # replaced by email
    email = models.EmailField(_("email address"), unique=True)
    phone = models.CharField(
        _("phone number"),
        max_length=32,
        blank=True,
        help_text=_("Optional. Shown as contact info on bookings when provided."),
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["email"]

    def __str__(self):
        return self.get_full_name() or self.email

    @property
    def is_resident(self):
        """Whether this account belongs to someone living in the association."""
        return hasattr(self, "resident")

    @property
    def is_business_committee(self):
        """Whether this user may see shop-rental applications."""
        return self.is_superuser or self.groups.filter(name=ERHVERVSUDVALG_GROUP).exists()


class Building(models.Model):
    """One entrance (opgang) of the association.

    The association spans several blocks on more than one street, so the street
    and number live here rather than being retyped on every resident — that
    keeps "Sankt Knuds Vej" from becoming three different spellings.
    """

    street = models.CharField(_("street"), max_length=128)
    house_number = models.CharField(
        _("house number"),
        max_length=16,
        help_text=_("Entrance number, e.g. '12' or '12A'."),
    )
    name = models.CharField(
        _("name"),
        max_length=64,
        blank=True,
        help_text=_("Optional internal name, e.g. 'Blok A'."),
    )

    class Meta:
        verbose_name = _("building")
        verbose_name_plural = _("buildings")
        ordering = ["street", "house_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["street", "house_number"],
                name="unique_building_address",
            )
        ]

    def __str__(self):
        return f"{self.street} {self.house_number}"


class Resident(models.Model):
    """A person living in the association, and where they live.

    `external_user_id` is the same person's id in the association's other
    database, which is the source of truth for who lives here. It is required:
    residents are expected to originate there rather than be typed in by hand.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="resident",
        verbose_name=_("user"),
    )
    external_user_id = models.BigIntegerField(
        _("external user ID"),
        unique=True,
        help_text=_("The user's ID in the association's other database."),
    )
    resident_number = models.CharField(
        _("resident number"),
        max_length=16,
        unique=True,
        validators=[resident_number_validator],
        help_text=_("Format: 1-2345-6789-0."),
    )
    building = models.ForeignKey(
        Building,
        on_delete=models.PROTECT,
        related_name="residents",
        verbose_name=_("building"),
    )
    floor = models.CharField(
        _("floor"),
        max_length=8,
        help_text=_("E.g. 'st.', '1', '2'. Use 'kl.' for basement."),
    )
    door = models.CharField(
        _("door"),
        max_length=16,
        blank=True,
        help_text=_("E.g. 'th', 'tv', 'mf', or a door number. Blank if the floor is one flat."),
    )

    class Meta:
        verbose_name = _("resident")
        verbose_name_plural = _("residents")
        # Deliberately no uniqueness on the address: several people can live in
        # the same flat and each may need their own login.
        ordering = ["building", "floor", "door"]

    def __str__(self):
        return f"{self.user} — {self.address}"

    @property
    def address(self):
        """Full address on one line, e.g. 'Sankt Knuds Vej 12, 3. th'."""
        home = self.floor if self.floor.endswith(".") else f"{self.floor}."
        if self.door:
            home = f"{home} {self.door}"
        return f"{self.building}, {home}"
