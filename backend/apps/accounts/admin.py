from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import Building, Resident, User


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
