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

`SignupRequest` is a third thing: somebody claiming, from the public signup
page, that they live here. It is deliberately not a `Resident` — see its
docstring.
"""

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
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


class SignupRequestStatus(models.TextChoices):
    PENDING = "pending", _("Afventer godkendelse")
    APPROVED = "approved", _("Godkendt")
    REJECTED = "rejected", _("Afvist")


class SignupRequest(models.Model):
    """Somebody claiming, from the public signup page, that they live here.

    A stranger filling in a form cannot be trusted about their own address, so
    signup creates the `User` **inactive** and a board member approves it. That
    approval is doing two jobs at once, which is why the portal can offer signup
    before either of its obvious prerequisites exists:

    * It stands in for the resident import. The board can check a claim against
      the association's other database by looking, today; what is missing is the
      automated sync, not the data.
    * It stands in for email confirmation. A confirmation link proves an address
      routes to the person signing up; a human comparing a claim to the resident
      register proves considerably more. Nothing can log in until someone has
      looked, so an unverified address is not a way in.

    **The claimed address is free text, and lives here rather than on
    `Resident`.** `Resident` is owned by the association's other database:
    `external_user_id` is required, and `resident_number` is unique, so a
    hand-typed number could collide with the same person's real row when the
    import finally runs. Approval therefore grants a login and nothing more — a
    newly approved user has no `Resident` row, which the rest of the portal
    already treats as ordinary (see `User.is_resident`). Attaching residency
    stays a separate, deliberate act in the admin.

    The claim is not format-validated either. A resident number is copied off a
    rent statement and mistyped often; refusing the signup teaches the applicant
    nothing, whereas a board member reading "1-2345-6789" next to a name and an
    address loses no information at all. The board is the validator here.

    The signup form does require a resident number, because the form is for
    residents: someone with no number to give is an employee or a third-party
    manager, and the board creates those accounts in the admin instead. The
    column stays `blank=True` all the same — it records what a request arrived
    with, and a board member entering one by hand from a phone call should not
    be blocked by a field the applicant never filled in.

    `email` is stored alongside the FK because rejection deletes the provisional
    account, and the audit row must outlive it — see `reject`.
    """

    email = models.EmailField(
        _("email"),
        help_text=_("Kopi af den oprettede brugers email, så rækken overlever en afvisning."),
    )
    user = models.OneToOneField(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signup_request",
        verbose_name=_("bruger"),
        help_text=_("Den inaktive konto, anmodningen oprettede. Tom når anmodningen er afvist."),
    )
    claimed_address = models.CharField(
        _("oplyst adresse"),
        max_length=255,
        help_text=_(
            "Fritekst, som ansøgeren skrev den. Sammenholdes med beboerregistret i hånden."
        ),
    )
    claimed_resident_number = models.CharField(
        _("oplyst beboernummer"),
        max_length=32,
        blank=True,
        help_text=_(
            "Krævet på tilmeldingssiden, men bevidst ikke formatvalideret. "
            "Bruges til at finde personen i beboerregistret."
        ),
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=SignupRequestStatus.choices,
        default=SignupRequestStatus.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(_("oprettet"), auto_now_add=True)
    status_changed_at = models.DateTimeField(
        _("status ændret"),
        null=True,
        blank=True,
        help_text=_("Gør det muligt at se, hvor længe en anmodning har ventet."),
    )
    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_signup_requests",
        verbose_name=_("behandlet af"),
    )
    review_note = models.TextField(
        _("bemærkning"),
        blank=True,
        help_text=_("Intern note om beslutningen. Vises ikke for ansøgeren."),
    )

    class Meta:
        verbose_name = _("brugeranmodning")
        verbose_name_plural = _("brugeranmodninger")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email} ({self.get_status_display()})"

    @property
    def is_pending(self):
        return self.status == SignupRequestStatus.PENDING

    def approve(self, *, by=None):
        """Let the account log in.

        Activation and the audit stamp go together in one call so a board member
        cannot approve the request and forget the account, or activate the
        account and leave the request looking unanswered.
        """
        if not self.is_pending:
            raise ValueError("Kun anmodninger, der afventer godkendelse, kan godkendes.")
        if self.user is None:
            raise ValueError("Anmodningen har ingen konto at aktivere.")
        self.user.is_active = True
        self.user.save(update_fields=["is_active"])
        self._record(SignupRequestStatus.APPROVED, by=by)

    def reject(self, *, by=None, note=""):
        """Turn the claim down and delete the account it created.

        Deleting rather than leaving the account inactive forever is what keeps
        the email address available. `User.email` is unique, so a pending
        request holds its address hostage: someone signing up as a resident who
        has not got around to it yet — whether maliciously or by typing the
        wrong address — would otherwise lock the real resident out of ever
        registering, and the portal cannot say "that email is taken" without
        confirming to a stranger who has an account here.

        Only a pending request can be rejected. Withdrawing access from an
        approved account is `is_active = False` on the user, not this: by then
        the account may own bookings that a cascade would take with it.
        """
        if not self.is_pending:
            raise ValueError("Kun anmodninger, der afventer godkendelse, kan afvises.")
        if note:
            self.review_note = note
        user, self.user = self.user, None
        self._record(SignupRequestStatus.REJECTED, by=by)
        if user is not None:
            user.delete()

    def _record(self, status, *, by):
        self.status = status
        self.status_changed_at = timezone.now()
        self.reviewed_by = by
        self.save(
            update_fields=["status", "status_changed_at", "reviewed_by", "review_note", "user"]
        )
