"""
Admin for shop-rental applications.

The committee works in the SPA, not here — this exists for the cases the SPA
deliberately does not cover: inspecting what the sync actually stored, fixing a
row by hand, and deleting a duplicate that a corrected email in the sheet left
behind.

The applicant's own answers are read-only throughout. They are a record of what
was submitted, and the admin is not the place to start editing history; the
contract details are where corrected information belongs.
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Application, ApplicationComment, ApplicationDetails


class ApplicationCommentInline(admin.TabularInline):
    model = ApplicationComment
    extra = 0
    fields = ["author", "body", "created_at"]
    readonly_fields = ["created_at"]


class ApplicationDetailsInline(admin.StackedInline):
    """Contract details are edited on the application, so there is one place
    to look — the same shape as residency on a user."""

    model = ApplicationDetails
    can_delete = False
    extra = 0
    verbose_name_plural = _("kontraktoplysninger")


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    inlines = [ApplicationDetailsInline, ApplicationCommentInline]
    list_display = ["applicant_name", "email", "submitted_at", "status", "rating", "assignee"]
    list_filter = ["status", "rating", "assignee"]
    search_fields = ["applicant_name", "email", "phone"]
    date_hierarchy = "submitted_at"
    # Everything the applicant supplied, plus the sync's own bookkeeping.
    readonly_fields = [
        "submitted_at",
        "applicant_name",
        "email",
        "phone",
        "answers",
        "source_key",
        "source_row",
        "imported_at",
        "synced_at",
        "status_changed_at",
    ]
    fieldsets = (
        (
            _("Fra formularen"),
            {
                "fields": ("submitted_at", "applicant_name", "email", "phone", "answers"),
                "description": _(
                    "Ansøgerens egne svar. Kun læsning — synkroniseringen ejer disse felter."
                ),
            },
        ),
        (
            _("Erhvervsudvalgets behandling"),
            {"fields": ("status", "status_changed_at", "rating", "assignee")},
        ),
        (
            _("Synkronisering"),
            {
                "classes": ("collapse",),
                "fields": ("source_key", "source_row", "imported_at", "synced_at"),
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("assignee", "details")

    def has_add_permission(self, request):
        """Applications come from the form, not from here. Adding one by hand
        would create a row with no `source_key`, which the next sync would then
        duplicate."""
        return False


@admin.register(ApplicationComment)
class ApplicationCommentAdmin(admin.ModelAdmin):
    list_display = ["application", "author", "created_at"]
    list_filter = ["author"]
    search_fields = ["body"]
    readonly_fields = ["created_at"]
