"""
Shop-rental applications (erhvervslejemål).

Three models, split by *who owns the data*, because that split is the whole
design:

* `Application` — what the applicant sent through the public Google Form. The
  sync owns these fields and overwrites them on every run.
* `ApplicationComment` and the workflow fields on `Application` (status, rating,
  assignee) — what the erhvervsudvalg decided. The sync must never touch these.
* `ApplicationDetails` — the concrete facts (CVR, rent, area, purpose) that only
  emerge once someone has been in contact. Filled in by hand, gradually, and
  eventually the raw material for the document handed to the association's
  lawyer to draft the actual lease.

**The form is expected to change.** Its questions are simple today and will be
reworded and added to, so this app does not mirror them as columns. Every answer
is kept verbatim and in order in `Application.answers`, and only the three
fields the committee filters and sorts on — name, email, phone — are lifted out
into real columns. `apps/shoprentals/ingest.py` holds the header aliases that do
that lifting, and it is the only place a form change needs to be reflected: an
unrecognised question still arrives, still displays, and is never lost.

Everything here is visible only to the erhvervsudvalg, so every endpoint in the
app is gated by `apps.accounts.permissions.IsBusinessCommittee`.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

#: CVR numbers are eight digits. Blank until someone asks the applicant for it.
cvr_validator = RegexValidator(
    regex=r"^\d{8}$",
    message=_("CVR-nummer skal være otte cifre."),
)


def searchable_text(applicant_name, email, phone, answers):
    """Flatten a submission into one blob of text for the search box.

    A denormalised column rather than a lookup into `answers`: Django writes
    JSONField values with `ensure_ascii`, so "Frisør" is stored as "Fris\\u00f8r"
    and an `icontains` against the JSON silently matches nothing for exactly the
    words a Danish committee searches for. Written on every sync alongside the
    answers themselves, so it cannot drift from them.
    """
    parts = [applicant_name, email, phone]
    parts += [
        f"{item.get('question', '')} {item.get('value', '')}"
        for item in answers or []
        if isinstance(item, dict)
    ]
    return "\n".join(part for part in parts if part)


class ApplicationStatus(models.TextChoices):
    """Where an application sits in the committee's process.

    These are the five states the committee actually works in, and the reason
    each exists is worth recording, because a shorter list has been proposed and
    does not survive contact with the work:

    * `NEW` — arrived, nobody has looked at it. The default.
    * `IN_PROGRESS` — someone is in contact. This can last months: agreeing a
      lease is slow, and an application sitting here is not a stalled one.
    * `SAVED` — a good applicant with no vacant unit to offer. Kept on purpose,
      to go back to when something frees up. This is the state that makes the
      list worth keeping at all.
    * `REJECTED` — not interesting. Filed away rather than deleted, so the same
      applicant reapplying is recognisable.
    * `FINISHED` — done with, contract signed or otherwise concluded.
    """

    NEW = "new", _("Ny")
    IN_PROGRESS = "in_progress", _("I gang")
    SAVED = "saved", _("Gemt til senere")
    REJECTED = "rejected", _("Afvist")
    FINISHED = "finished", _("Afsluttet")


#: Statuses that still want someone's attention — the committee's working set.
OPEN_STATUSES = (ApplicationStatus.NEW, ApplicationStatus.IN_PROGRESS, ApplicationStatus.SAVED)


class Application(models.Model):
    """One submission of the public shop-rental form, plus what we made of it.

    Rows are created and refreshed by `python manage.py sync_applications`. The
    committee's own work lives on the same row but in fields the sync leaves
    alone — see `apply_form_data`, which is the only writer of form-owned fields
    and deliberately writes nothing else.
    """

    # --- identity in the responses sheet ---------------------------------
    #
    # A Forms response sheet gives us no stable id of its own, so the key is
    # derived from the submission itself (see ingest.source_key). Unique, so a
    # re-sync updates the existing row instead of duplicating it.
    source_key = models.CharField(_("kildenøgle"), max_length=64, unique=True, editable=False)
    source_row = models.PositiveIntegerField(
        _("række i regnearket"),
        null=True,
        blank=True,
        editable=False,
        help_text=_("Kun til fejlsøgning. Rækker flytter sig, når regnearket sorteres."),
    )

    # --- what the applicant sent -----------------------------------------
    submitted_at = models.DateTimeField(
        _("indsendt"),
        help_text=_("Tidsstemplet fra formularen."),
    )
    applicant_name = models.CharField(_("ansøger"), max_length=200, blank=True)
    email = models.EmailField(_("email"), blank=True)
    phone = models.CharField(_("telefon"), max_length=64, blank=True)
    answers = models.JSONField(
        _("svar"),
        default=list,
        help_text=_(
            "Alle formularens svar, i formularens rækkefølge: [{'question': ..., 'value': ...}]."
        ),
    )
    # Derived from the fields above on every sync — see `searchable_text`. Not
    # editable: it is a search index, not information.
    search_text = models.TextField(_("søgetekst"), blank=True, editable=False)

    # --- what the committee decided --------------------------------------
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.NEW,
        db_index=True,
    )
    status_changed_at = models.DateTimeField(
        _("status ændret"),
        null=True,
        blank=True,
        help_text=_("Gør det muligt at se, hvor længe en ansøgning har stået stille."),
    )
    rating = models.PositiveSmallIntegerField(
        _("vurdering"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text=_("1–5. Tom betyder ikke vurderet, hvilket ikke er det samme som 1."),
    )
    # SET_NULL, not PROTECT: unassigned is a perfectly good state, and a
    # committee member leaving should not be blocked by the applications they
    # happened to be looking after.
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_applications",
        verbose_name=_("ansvarlig"),
    )

    # --- bookkeeping -----------------------------------------------------
    imported_at = models.DateTimeField(_("importeret"), auto_now_add=True)
    synced_at = models.DateTimeField(
        _("senest synkroniseret"),
        null=True,
        blank=True,
        help_text=_("Sidste gang formularens svar blev læst ind på denne ansøgning."),
    )
    updated_at = models.DateTimeField(_("opdateret"), auto_now=True)

    class Meta:
        verbose_name = _("erhvervsansøgning")
        verbose_name_plural = _("erhvervsansøgninger")
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["status", "-submitted_at"]),
        ]

    def __str__(self):
        who = self.applicant_name or self.email or _("ukendt ansøger")
        return f"{who} — {timezone.localtime(self.submitted_at):%d-%m-%Y}"

    def save(self, *args, **kwargs):
        """Keep `search_text` in step with the answers, whoever writes the row.

        Derived here rather than at each call site so it cannot drift: the sync,
        the admin, a shell session and a test fixture all get a searchable row
        without having to remember to build one.
        """
        self.search_text = searchable_text(
            self.applicant_name, self.email, self.phone, self.answers
        )
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "search_text" not in update_fields:
            kwargs["update_fields"] = [*update_fields, "search_text"]
        super().save(*args, **kwargs)

    @property
    def is_open(self):
        """Whether this application is still in the committee's working set."""
        return self.status in OPEN_STATUSES

    def set_status(self, status, *, save=True):
        """Change status and stamp when it happened.

        Going through here rather than assigning `status` directly is what keeps
        `status_changed_at` honest — and knowing an application has been "I gang"
        since March is the point of recording it.
        """
        if status == self.status:
            return
        self.status = status
        self.status_changed_at = timezone.now()
        if save:
            self.save(update_fields=["status", "status_changed_at", "updated_at"])

    def apply_form_data(self, *, submitted_at, applicant_name, email, phone, answers, source_row):
        """Write the form-owned fields, and only those.

        Called by the sync on every run, including for applications the
        committee has already worked on. The list of fields it touches must stay
        exactly the applicant's own answers: status, rating, assignee, comments
        and the contract details are the committee's, and a reconciliation pass
        that quietly reset someone's rating to nothing would make the sync
        actively dangerous to run.

        Returns True when something actually changed, so the command can report
        a useful count instead of "300 rows updated" every five minutes.
        """
        incoming = {
            "submitted_at": submitted_at,
            "applicant_name": applicant_name,
            "email": email,
            "phone": phone,
            "answers": answers,
            "source_row": source_row,
        }
        changed = [field for field, value in incoming.items() if getattr(self, field) != value]
        for field, value in incoming.items():
            setattr(self, field, value)
        self.synced_at = timezone.now()
        self.save(update_fields=[*changed, "synced_at", "updated_at"])
        return bool(changed)


class ApplicationComment(models.Model):
    """A committee member's note on an application.

    One row per comment rather than a single text field on the application, so
    it is always clear who said what and when — these threads run for months and
    a shared scratchpad loses that immediately.
    """

    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="comments",
        verbose_name=_("ansøgning"),
    )
    # PROTECT: a comment with its author erased is worse than no comment.
    # Deactivate departed members rather than deleting them.
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="application_comments",
        verbose_name=_("skrevet af"),
    )
    body = models.TextField(_("kommentar"))
    created_at = models.DateTimeField(_("skrevet"), auto_now_add=True)

    class Meta:
        verbose_name = _("kommentar")
        verbose_name_plural = _("kommentarer")
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.author} — {self.created_at:%d-%m-%Y}"


class ApplicationDetails(models.Model):
    """The concrete facts about an applicant, gathered after making contact.

    None of this comes from the form: the form asks only enough to judge whether
    an applicant is worth talking to, and the rest — CVR, what they intend to
    use the premises for, the rent and area agreed, when they want to start —
    arrives over weeks of correspondence. So every field here is optional, and
    a half-filled record is the normal state rather than an error.

    The purpose of collecting it in one place is the document the committee hands
    to the association's lawyer, who drafts the lease from it. `missing_fields`
    is what turns that into a checklist: it names what still has to be asked.

    The field list is a starting point rather than a legal instrument — it should
    be reconciled with what the lawyer actually asks for, and adding a field here
    is a migration and nothing more.
    """

    application = models.OneToOneField(
        Application,
        on_delete=models.CASCADE,
        related_name="details",
        verbose_name=_("ansøgning"),
    )

    # --- the legal entity ------------------------------------------------
    company_name = models.CharField(_("virksomhedsnavn"), max_length=200, blank=True)
    cvr = models.CharField(_("CVR-nummer"), max_length=8, blank=True, validators=[cvr_validator])
    legal_form = models.CharField(
        _("selskabsform"),
        max_length=64,
        blank=True,
        help_text=_("F.eks. ApS, A/S, enkeltmandsvirksomhed."),
    )
    company_address = models.TextField(_("virksomhedens adresse"), blank=True)

    # --- who signs and who we talk to ------------------------------------
    contact_name = models.CharField(_("kontaktperson"), max_length=200, blank=True)
    contact_email = models.EmailField(_("kontaktemail"), blank=True)
    contact_phone = models.CharField(_("kontakttelefon"), max_length=64, blank=True)

    # --- the lease itself ------------------------------------------------
    unit_label = models.CharField(
        _("lejemål"),
        max_length=120,
        blank=True,
        help_text=_("Hvilket lejemål ansøgningen gælder, f.eks. 'Jægergade 4, kld.'."),
    )
    purpose = models.TextField(
        _("anvendelse"),
        blank=True,
        help_text=_("Hvad lejemålet må bruges til. Står i kontrakten, så vær konkret."),
    )
    area_sqm = models.DecimalField(
        _("areal (m²)"), max_digits=8, decimal_places=2, null=True, blank=True
    )
    annual_rent_dkk = models.DecimalField(
        _("årlig leje (kr.)"), max_digits=12, decimal_places=2, null=True, blank=True
    )
    deposit_months = models.PositiveSmallIntegerField(
        _("depositum (måneder)"), null=True, blank=True
    )
    lease_start = models.DateField(_("ønsket overtagelse"), null=True, blank=True)
    notes = models.TextField(
        _("noter til advokaten"),
        blank=True,
        help_text=_("Aftaler og forbehold, der ikke passer i felterne ovenfor."),
    )

    updated_at = models.DateTimeField(_("opdateret"), auto_now=True)

    #: What the lawyer needs before a lease can be drafted at all. Kept as a
    #: tuple of field names so `missing_fields` reports them with their own
    #: Danish labels rather than a second, drifting list of strings.
    REQUIRED_FOR_CONTRACT = (
        "company_name",
        "cvr",
        "contact_name",
        "contact_email",
        "unit_label",
        "purpose",
        "area_sqm",
        "annual_rent_dkk",
        "lease_start",
    )

    class Meta:
        verbose_name = _("kontraktoplysninger")
        verbose_name_plural = _("kontraktoplysninger")

    def __str__(self):
        return f"{_('Kontraktoplysninger')} — {self.application}"

    @property
    def missing_fields(self):
        """Danish labels of the contract-critical fields still left blank."""
        return [
            str(self._meta.get_field(name).verbose_name)
            for name in self.REQUIRED_FOR_CONTRACT
            if getattr(self, name) in (None, "")
        ]

    @property
    def is_complete(self):
        """Whether there is enough here to send to the lawyer."""
        return not self.missing_fields
