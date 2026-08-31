from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from .models import Building, Resident, SignupRequest, SignupRequestStatus, User


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
        "resident__external_user_id",
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
