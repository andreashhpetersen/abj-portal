from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import BookingSettings, Event, EventAttendance, EventSeries


class AttendanceInline(admin.TabularInline):
    model = EventAttendance
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    inlines = [AttendanceInline]
    list_display = ["__str__", "category", "start", "end", "created_by", "status"]
    list_filter = ["category", "cancelled_at", "start"]
    search_fields = ["title", "description", "created_by__email"]
    date_hierarchy = "start"
    autocomplete_fields = ["created_by"]
    actions = ["cancel_selected"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("created_by", "series")

    @admin.display(description=_("status"))
    def status(self, obj):
        if obj.is_cancelled:
            return _("cancelled")
        return _("upcoming") if obj.start > timezone.now() else _("past")

    @admin.action(description=_("Cancel selected events"))
    def cancel_selected(self, request, queryset):
        cancelled = 0
        for event in queryset.filter(cancelled_at__isnull=True):
            event.cancel(by=request.user)
            cancelled += 1
        self.message_user(request, _("%(count)d events cancelled.") % {"count": cancelled})


@admin.register(EventSeries)
class EventSeriesAdmin(admin.ModelAdmin):
    list_display = ["title", "frequency", "interval", "until", "occurrence_count"]
    list_filter = ["frequency"]
    search_fields = ["title", "description"]
    autocomplete_fields = ["created_by"]

    @admin.display(description=_("occurrences"))
    def occurrence_count(self, obj):
        return obj.occurrences.count()


@admin.register(BookingSettings)
class BookingSettingsAdmin(admin.ModelAdmin):
    """Singleton: no adding, no deleting, just edit the one row."""

    list_display = [
        "__str__",
        "private_bookings_enabled",
        "private_booking_min_notice_days",
        "private_booking_max_horizon_days",
        "private_booking_weekdays",
    ]

    def has_add_permission(self, request):
        return not BookingSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        BookingSettings.load()  # make sure the row exists before listing it
        return super().changelist_view(request, extra_context)
