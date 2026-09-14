from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from .emails import send_signup_approved_email
from .forms import RegisterUploadForm
from .models import (
    Building,
    RegisterEntry,
    Resident,
    SignupRequest,
    SignupRequestStatus,
    User,
)
from .register import (
    INFO,
    SUCCESS,
    WARNING,
    dry_run_notes,
    header_notes,
    match_claim,
    outcome_notes,
    parse_rows,
    run_import,
)

#: A `register.Note` level as the admin's message framework spells it. The
#: import's warnings are not errors — every real export produces a few — so they
#: land as warnings rather than as a red banner that invites undoing something
#: that worked.
LEVELS = {
    SUCCESS: messages.SUCCESS,
    INFO: messages.INFO,
    WARNING: messages.WARNING,
}


class ResidentInline(admin.StackedInline):
    """Residency is edited on the user, so there is one place to look."""

    model = Resident
    can_delete = True
    extra = 0
    verbose_name_plural = _("residency")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """UserAdmin rebuilt around email, since there is no username field."""

    inlines = [ResidentInline]
    ordering = ["email"]
    list_display = ["email", "first_name", "last_name", "residency", "is_staff"]
    list_filter = ["is_staff", "is_superuser", "is_active", "groups", "resident__building"]
    search_fields = [
        "email",
        "first_name",
        "last_name",
        "resident__resident_number",
        "resident__unit_number",
    ]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name", "phone")}),
        (
            _("Permissions"),
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("resident__building")

    @admin.display(description=_("residency"))
    def residency(self, obj):
        """Address for residents, and a clear marker for everyone else."""
        return obj.resident.address if obj.is_resident else _("not a resident")


@admin.register(Building)
class BuildingAdmin(admin.ModelAdmin):
    list_display = ["__str__", "name", "resident_count"]
    search_fields = ["street", "house_number", "name"]

    def get_queryset(self, request):
        from django.db.models import Count

        return super().get_queryset(request).annotate(_resident_count=Count("residents"))

    @admin.display(description=_("residents"), ordering="_resident_count")
    def resident_count(self, obj):
        return obj._resident_count


@admin.register(SignupRequest)
class SignupRequestAdmin(admin.ModelAdmin):
    """The board's queue of people claiming to live here.

    Everything the applicant wrote is read-only, the same discipline the
    shop-rental sync follows: the applicant owns their claim, the board owns the
    decision. Editing a claimed address in place would destroy the only record
    of what was actually submitted — which is the thing being checked.

    There is no add form. Requests exist because somebody filled in the signup
    page; a board member who wants to create an account outright can do it on
    the user itself, where the account is active from the start and no approval
    is pretended.
    """

    list_display = [
        "email",
        "applicant",
        "claimed_address",
        "claimed_resident_number",
        "register_match",
        "status",
        "created_at",
        "reviewed_by",
    ]
    list_filter = ["status", "created_at"]
    search_fields = ["email", "claimed_address", "claimed_resident_number"]
    date_hierarchy = "created_at"
    actions = ["approve_selected", "reject_selected"]
    readonly_fields = [
        "email",
        "user",
        "claimed_address",
        "claimed_resident_number",
        "register_match",
        "status",
        "created_at",
        "status_changed_at",
        "reviewed_by",
    ]
    fieldsets = (
        (
            _("Ansøgerens oplysninger"),
            {
                "fields": ("email", "user", "claimed_address", "claimed_resident_number"),
                "description": _(
                    "Oplyst af ansøgeren selv og derfor ikke bevis for noget. "
                    "Sammenhold med beboerregistret, før du godkender."
                ),
            },
        ),
        (
            _("Beboerregistret"),
            {
                "fields": ("register_match",),
                "description": _(
                    "Slås op nu, ikke da anmodningen kom ind, så den viser det seneste "
                    "importerede register. En anmodning, der ligger her, er en, registret "
                    "ikke kunne genkende — ellers var kontoen aktiveret automatisk. "
                    "Er der først kommet et match, så importér igen: så bliver den godkendt, "
                    "uden at du skal klikke."
                ),
            },
        ),
        (
            _("Bestyrelsens behandling"),
            {"fields": ("status", "status_changed_at", "reviewed_by", "review_note")},
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user", "reviewed_by")

    def has_add_permission(self, request):
        return False

    @admin.display(description=_("ansøger"))
    def applicant(self, obj):
        """The name on the provisional account, or a marker once it is gone."""
        if obj.user is None:
            return _("kontoen er slettet")
        return obj.user.get_full_name() or obj.user.email

    @admin.display(description=_("match i registret"))
    def register_match(self, obj):
        """What the claimed number resolves to in the register, looked up now.

        The lookup a board member would otherwise do in INNA's system, done for
        them — and done against the current register rather than the one that
        existed when the request arrived, which is the version that matters when
        deciding today.

        A pending request showing a match means the register has caught up since
        the person signed up. Importing again approves it; there is no need to
        click, and clicking is fine too.
        """
        match = match_claim(obj.claimed_resident_number, email=obj.email)
        if match is None:
            return _("Intet match — nummeret hører ikke til en andelsbolig i registret.")
        names = ", ".join(entry.full_name for entry in match.entries if entry.full_name)
        return _("%(address)s (bolignr. %(unit)s) — %(names)s") % {
            "address": match.entry.address,
            "unit": match.unit_number,
            "names": names or _("uden navn"),
        }

    @admin.action(description=_("Godkend valgte anmodninger og aktivér kontoen"))
    def approve_selected(self, request, queryset):
        self._apply(request, queryset, "approve")

    @admin.action(description=_("Afvis valgte anmodninger og slet kontoen"))
    def reject_selected(self, request, queryset):
        self._apply(request, queryset, "reject")

    def _apply(self, request, queryset, decision):
        """Run one decision over the selection, skipping the already-decided.

        Looping in Python rather than a bulk `update()` is the point: `approve`
        also activates the account and `reject` also deletes it, and a queryset
        update would silently do neither. The selection is filtered to pending
        so that re-running an action over a stale page cannot resurrect a
        rejected request or re-stamp a decision somebody else already made.
        """
        pending = queryset.filter(status=SignupRequestStatus.PENDING)
        done = 0
        for signup_request in pending:
            getattr(signup_request, decision)(by=request.user)
            if decision == "approve":
                # Only path that notifies the applicant: an auto-approved
                # signup is told on the spot instead, in its own response —
                # see `send_signup_approved_email`.
                send_signup_approved_email(signup_request, request)
            done += 1
        skipped = queryset.count() - done
        if done:
            self.message_user(
                request,
                ngettext("%d anmodning behandlet.", "%d anmodninger behandlet.", done) % done,
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                ngettext(
                    "%d anmodning blev sprunget over, fordi den allerede var behandlet.",
                    "%d anmodninger blev sprunget over, fordi de allerede var behandlet.",
                    skipped,
                )
                % skipped,
                messages.WARNING,
            )


@admin.register(RegisterEntry)
class RegisterEntryAdmin(admin.ModelAdmin):
    """INNA's resident register as last imported. Read-only, all of it.

    Nothing here is the portal's to edit: `manage.py import_residents`
    overwrites every column on the next run, so a correction made in this form
    would survive until somebody imported and then vanish without trace. A wrong
    row is fixed in INNA's own system and arrives on the next import. A residency
    that needs correcting *now* is corrected on the user instead, where the
    import leaves it alone.

    There is no add form either, for the same reason.

    The two filters worth having are `kan aktivere konto` — who the portal will
    let in, which is the question this whole table exists to answer — and `står
    i seneste udtræk`, which is how a board member finds the people who have
    quietly stopped appearing.
    """

    list_display = [
        "full_name",
        "address_or_raw",
        "unit_number",
        "resident_number",
        "unit_type",
        "role",
        "is_eligible",
        "is_current",
    ]
    list_filter = ["is_eligible", "is_current", "unit_type", "role", "building"]
    search_fields = [
        "first_name",
        "last_name",
        "alias",
        "email",
        "unit_number",
        "resident_number",
        "raw_address",
    ]
    ordering = ["unit_number", "name_key"]
    fieldsets = (
        (
            _("Person"),
            {"fields": ("first_name", "last_name", "alias", "email", "phone", "role")},
        ),
        (
            _("Bolig"),
            {
                "fields": (
                    "unit_number",
                    "resident_number",
                    "unit_type",
                    "raw_address",
                    "building",
                    "floor",
                    "door",
                    "postal_code",
                    "city",
                    "moved_in",
                ),
                "description": _(
                    "Adressen i registret er fritekst. Opgang, etage og dør er portalens "
                    "læsning af den — står de tomme, kunne adressen ikke læses som en bolig."
                ),
            },
        ),
        (
            _("Import"),
            {
                "fields": (
                    "is_eligible",
                    "is_current",
                    "name_key",
                    "first_seen_at",
                    "last_seen_at",
                    "source_row",
                )
            },
        ),
    )

    change_list_template = "admin/accounts/registerentry/change_list.html"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("building")

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_import_permission(self, request):
        """Its own permission, not `change`: see `RegisterEntry.Meta`."""
        return request.user.has_perm("accounts.import_register")

    def get_urls(self):
        return [
            path(
                "import/",
                self.admin_site.admin_view(self.import_view),
                name="accounts_registerentry_import",
            ),
            *super().get_urls(),
        ]

    def changelist_view(self, request, extra_context=None):
        """Show the import link only to somebody who may use it."""
        return super().changelist_view(
            request,
            {**(extra_context or {}), "has_import_permission": self.has_import_permission(request)},
        )

    def import_view(self, request):
        """Upload an export of the register and reconcile it.

        The file is read in the form and never stored: `RegisterEntry` rows are
        what an upload leaves behind, and the export itself — six hundred
        people's names, emails and home addresses — is gone when the request
        ends. That is also why a dry run cannot simply be confirmed with a
        second click: there would be nothing left to confirm against, and the
        alternative is keeping the file somewhere, which is the thing being
        avoided. Ticking the box off and choosing the file again is the price,
        and this is done a handful of times a year.

        A real import redirects to the changelist with the report in messages,
        so a refresh cannot run it twice. A dry run does not, because its report
        is the whole output and the form should still be there underneath it.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        form = RegisterUploadForm(request.POST or None, request.FILES or None)
        notes = []
        if request.method == "POST" and form.is_valid():
            header, rows = form.cleaned_data["file"]
            notes = header_notes(header)
            if form.cleaned_data["dry_run"]:
                parsed, skipped = parse_rows(header, rows)
                notes += dry_run_notes(parsed, skipped)
            else:
                notes += outcome_notes(run_import(header, rows))
                for note in notes:
                    self.message_user(request, note.text, LEVELS[note.level])
                return redirect(reverse("admin:accounts_registerentry_changelist"))

        return render(
            request,
            "admin/accounts/registerentry/import.html",
            {
                **self.admin_site.each_context(request),
                "title": _("Importér beboerregister"),
                "opts": self.model._meta,
                "form": form,
                "notes": notes,
            },
        )

    @admin.display(description=_("navn"), ordering="last_name")
    def full_name(self, obj):
        return obj.full_name or obj.alias or obj.name_key

    @admin.display(description=_("adresse"), ordering="unit_number")
    def address_or_raw(self, obj):
        """The parsed address, falling back to whatever the register wrote.

        Never blank: a storage room whose address could not be read is still
        worth seeing in the list, and the raw text is what identifies it.
        """
        return obj.address or obj.raw_address
