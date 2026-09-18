"""
Accounts and residency.

Two things are deliberately kept apart:

* `User` is *anyone who can log in* — residents, but also employees and
  third-party managers who have no home in the association.
* `Resident` is the residency itself: which flat, and which numbers the
  association's administrator knows it by.

Modelling it this way means an employee account simply has no Resident row
rather than a spread of empty columns.

Two more things sit alongside them, and the distinction between all four is
worth holding on to:

* `RegisterEntry` is a row of INNA's resident register as last exported —
  everyone the administrator knows about, whether or not they have ever used
  the portal. It is the list a claim is checked *against*, not a login.
* `SignupRequest` is somebody claiming, from the public signup page, that they
  live here. It is deliberately not a `Resident` — see its docstring.

So: the register says who lives here, a signup request says who says they do,
and a `Resident` row exists only where a login and the register have been
matched up. `apps/accounts/register.py` is what does the matching.
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

#: Members of this Django group may create a public booking while
#: `BookingSettings.public_bookings_open` is off, and book one in someone
#: else's name. Danish for "the beboerlokale's group" — a group rather than a
#: committee like ERHVERVSUDVALG_GROUP, but the same naming convention.
BEBOERLOKALEGRUPPE_GROUP = "beboerlokalegruppe"

#: A *unit* number — `Bolignr.` in INNA's register. It identifies a flat, a
#: shop or a storage room, and unlike everything else about a residency it does
#: not change: people move, tenancies are renumbered, the unit stays. That is
#: why it, rather than the resident number, is what links a `Resident` row back
#: to the register.
unit_number_validator = RegexValidator(
    regex=r"^\d-\d{3,5}-\d{3,5}$",
    message=_("Bolignummer skal have formatet 1-1121-4203."),
)

#: A *tenancy* number — `Beboernr.` in INNA's register, and the number printed
#: on a rent statement, which is why the signup form asks for it.
#:
#: Two things about it are easy to assume and wrong. It is not unique: the
#: people sharing a flat share one number, so a couple has two logins and one
#: `Beboernr.`. And the group widths vary — the register contains `1-1121-409-2`
#: and `1-1121-5007-10` beside the usual `1-1121-4203-2` — so a validator
#: pinned to the common shape rejects real residents.
resident_number_validator = RegexValidator(
    regex=r"^\d-\d{3,5}-\d{3,5}-\d{1,3}$",
    message=_("Beboernummer skal have formatet 1-2345-6789-0."),
)


def format_address(building, floor, door):
    """One line of address, e.g. 'Sankt Knuds Vej 12, 3. th'.

    Shared by `Resident` and `RegisterEntry` so the portal cannot end up
    displaying a flat two different ways depending on which of them it read.
    """
    if building is None:
        return ""
    home = floor if floor.endswith(".") else f"{floor}."
    if door:
        home = f"{home} {door}"
    return f"{building}, {home}"


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

    @property
    def is_event_organizer(self):
        """Whether this user may create a public booking while it is
        restricted, and book one in someone else's name — see
        `apps.bookings.models.BookingSettings.public_bookings_open`."""
        return self.is_superuser or self.groups.filter(name=BEBOERLOKALEGRUPPE_GROUP).exists()


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


class RegisterEntryQuerySet(models.QuerySet):
    def current(self):
        """Entries the most recent export still listed."""
        return self.filter(is_current=True)

    def eligible(self):
        """Entries whose person may activate an account.

        The rule itself is applied once, at import, by
        `register.mark_eligibility` — it needs the whole export to decide, since
        a household member's eligibility depends on whether their flat is
        residential. Stored rather than recomputed, so what the portal admits is
        always what the last import found rather than a query that has quietly
        drifted from it. `current()` on top of it because an entry that stopped
        appearing must stop admitting people the same day.
        """
        return self.current().filter(is_eligible=True)


class RegisterEntry(models.Model):
    """One person in one unit, as INNA's resident register last described them.

    This is the list a signup is checked against. It is not a list of accounts
    and importing it creates none: six hundred people are in the register, most
    have never opened the portal, and thirty of the flats have no email address
    in it at all, so there is nothing to create an account *from*. What the
    import produces is the ability to answer "does this claim match somebody who
    lives here" without a board member logging in to INNA's system.

    **Identity is the unit plus the name.** The export has no id for a person.
    `Bolignr.` identifies the unit and `Beboernr.` the tenancy; two people
    sharing a flat share both, so neither tells them apart, and a line number
    would re-point every row below it as soon as somebody sorted the export.
    The name is what is left, and it works because the pair only has to be
    unique within one flat. The cost is that a corrected name reads as a new
    person and the old row goes `is_current=False` on the next import — visible,
    and in the admin, which is the right place for a human to notice.

    **Nothing here is the portal's to edit.** Every column is overwritten by the
    next import, which is why the admin shows them read-only. The portal's own
    view of a residency lives on `Resident`, where a board member's correction
    survives.
    """

    unit_number = models.CharField(
        _("bolignr."),
        max_length=16,
        db_index=True,
        validators=[unit_number_validator],
        help_text=_("Enheden — lejligheden, butikken eller rummet. Ændrer sig ikke."),
    )
    name_key = models.CharField(
        _("navnenøgle"),
        max_length=300,
        help_text=_("Navnet i sammenlignbar form. Sammen med bolignr. identificerer det rækken."),
    )
    first_name = models.CharField(_("fornavn"), max_length=150, blank=True)
    last_name = models.CharField(_("efternavn"), max_length=150, blank=True)
    alias = models.CharField(
        _("alias"),
        max_length=200,
        blank=True,
        help_text=_("Kaldenavn eller firmanavn, som registret skriver det."),
    )
    email = models.EmailField(_("email"), blank=True)
    phone = models.CharField(_("telefon"), max_length=32, blank=True)
    role = models.CharField(
        _("rolle"),
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_("'Role hos CS': Juridisk navn/bruger, Husstandsmedlem, Fremlejertager, …"),
    )
    unit_type = models.CharField(
        _("enhedstype"),
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_("Andelsbolig, Erhverv, Loft/Kælder/Depotrum. Tom for husstandsmedlemmer."),
    )
    resident_number = models.CharField(
        _("beboernr."),
        max_length=16,
        blank=True,
        db_index=True,
        validators=[resident_number_validator],
        help_text=_("Lejemålet. Deles af husstanden, og tomt for husstandsmedlemmer."),
    )
    raw_address = models.CharField(
        _("adresse i registret"),
        max_length=255,
        blank=True,
        help_text=_("Ordret som registret skriver den, også når den ikke kunne læses."),
    )
    postal_code = models.CharField(_("postnr."), max_length=16, blank=True)
    city = models.CharField(_("by"), max_length=64, blank=True)
    building = models.ForeignKey(
        Building,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="register_entries",
        verbose_name=_("opgang"),
        help_text=_("Tom når adressen ikke kunne læses — typisk et kælder- eller depotrum."),
    )
    floor = models.CharField(_("etage"), max_length=8, blank=True)
    door = models.CharField(_("dør"), max_length=16, blank=True)
    moved_in = models.DateField(
        _("indflytningsdato"),
        null=True,
        blank=True,
        help_text=_(
            "Kan ligge i fremtiden. Har bevidst ingen betydning for, om kontoen kan aktiveres."
        ),
    )
    is_eligible = models.BooleanField(
        _("kan aktivere konto"),
        default=False,
        db_index=True,
        help_text=_("Bor i en andelsbolig ifølge sidste import. Sat af importen, ikke i hånden."),
    )
    is_current = models.BooleanField(
        _("står i seneste udtræk"),
        default=True,
        db_index=True,
        help_text=_("Falsk når rækken er forsvundet fra registret — typisk en fraflytning."),
    )
    first_seen_at = models.DateTimeField(_("set første gang"), default=timezone.now)
    last_seen_at = models.DateTimeField(
        _("set sidste gang"),
        default=timezone.now,
        help_text=_("Det seneste udtræk, personen faktisk stod i."),
    )
    source_row = models.PositiveIntegerField(
        _("linje i udtrækket"),
        null=True,
        blank=True,
        help_text=_("Kun til fejlsøgning i den downloadede fil."),
    )

    objects = RegisterEntryQuerySet.as_manager()

    class Meta:
        verbose_name = _("beboerregisterrække")
        verbose_name_plural = _("beboerregister")
        ordering = ["unit_number", "name_key"]
        # Its own permission rather than `change_registerentry`, because the two
        # are not the same thing: every column here is read-only in the admin,
        # and what this grants is the right to replace the whole register from a
        # file — and with it, who the portal lets in without asking anybody.
        # Separate so the board can hand it to the person who actually fetches
        # the export, without handing over the rest of the admin.
        permissions = [("import_register", _("Kan importere beboerregistret fra en fil"))]
        constraints = [
            models.UniqueConstraint(
                fields=["unit_number", "name_key"],
                name="unique_register_entry_per_unit_and_name",
            )
        ]

    def __str__(self):
        return f"{self.full_name or self.name_key} — {self.address or self.raw_address}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def address(self):
        """The parsed address, or "" when it could not be read."""
        return format_address(self.building, self.floor, self.door)


class Resident(models.Model):
    """A person living in the association, and where they live.

    Filled in from INNA's register rather than typed: a signup that matches an
    eligible `RegisterEntry` gets one of these, and every later import keeps it
    in step. A board member can still create one by hand for somebody the
    register has not caught up with.

    **`unit_number` is the link back to the register, not `resident_number`.**
    The unit is the stable thing — the tenancy number changes when a flat
    changes hands, and is shared by the people living in it, so it identifies
    neither the person nor reliably the flat over time. Both columns are
    `blank=True` because the register does not always have both: a household
    member registered against their partner's flat has a unit and no tenancy
    number at all.

    There is deliberately no id-of-this-person-elsewhere column. The export
    INNA provides has none — its only identifiers are the unit and the tenancy —
    and a synthesised one would be a number that means nothing anywhere.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="resident",
        verbose_name=_("user"),
    )
    unit_number = models.CharField(
        _("unit number"),
        max_length=16,
        blank=True,
        db_index=True,
        validators=[unit_number_validator],
        help_text=_("Bolignr. i beboerregistret. Format: 1-1121-4203."),
    )
    resident_number = models.CharField(
        _("resident number"),
        max_length=16,
        blank=True,
        db_index=True,
        validators=[resident_number_validator],
        help_text=_("Beboernr. i beboerregistret. Format: 1-2345-6789-0. Deles af en husstand."),
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
        return format_address(self.building, self.floor, self.door)

    @property
    def register_entries(self):
        """The register rows describing this residency, newest export first.

        A query rather than a foreign key on purpose. A register row has no id
        of its own — it is identified by its unit and the name on it — so a FK
        would break the moment INNA corrected a spelling, and break silently.
        The unit number survives all of that.
        """
        if not self.unit_number:
            return RegisterEntry.objects.none()
        return RegisterEntry.objects.filter(unit_number=self.unit_number)


class SignupRequestStatus(models.TextChoices):
    PENDING = "pending", _("Afventer godkendelse")
    APPROVED = "approved", _("Godkendt")
    REJECTED = "rejected", _("Afvist")


class SignupRequest(models.Model):
    """Somebody claiming, from the public signup page, that they live here.

    A stranger filling in a form cannot be trusted about their own address, so
    the claim is checked before it becomes a login. `register.auto_approve` tries
    the register first: a claimed number that resolves to one eligible flat
    activates the account immediately, because living in an andelsbolig is what
    entitles somebody to the portal and the register is where that fact lives.
    Everything the register cannot vouch for lands here as a pending request and
    waits for a board member, with the account created **inactive** meanwhile.

    So this model is now the exception queue rather than the whole gate, and it
    has to keep working as one: it is what let the portal offer signup before
    the register existed at all, and it is still the answer for a number typed
    one digit out, a flat the export has not caught up with, and anyone the
    register was never going to know about.

    **It is still not a `Resident`.** What is stored is what the applicant typed,
    free text, because that is the thing being checked — writing it into a
    residency would destroy the only record of the claim, and would assert an
    address on the association's behalf that nothing has confirmed. Residency
    comes from the register or from a board member, never from this form.

    The claim is not format-validated either. A resident number is copied off a
    rent statement and mistyped often; refusing the signup teaches the applicant
    nothing, whereas a mistyped number simply fails to match and a board member
    reading "1-2345-6789" next to a name and an address loses no information at
    all.

    There is no email confirmation, and the approval used to stand in for one: a
    human comparing a claim to the register proved considerably more than a link
    proving an address routes somewhere. Automatic approval does not have that
    property, which is the trade `register.auto_approve` documents — and the
    reason the per-IP signup throttle now matters as much as the honeypot.

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

    def approve(self, *, by=None, note=""):
        """Let the account log in.

        Activation and the audit stamp go together in one call so a board member
        cannot approve the request and forget the account, or activate the
        account and leave the request looking unanswered.

        `by` is None when the register approved it rather than a person — see
        `register.auto_approve`, which passes a `note` saying which entry it
        matched, so an approval with no reviewer is never a mystery.
        """
        if not self.is_pending:
            raise ValueError("Kun anmodninger, der afventer godkendelse, kan godkendes.")
        if self.user is None:
            raise ValueError("Anmodningen har ingen konto at aktivere.")
        if note:
            self.review_note = note
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
