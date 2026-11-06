"""Django admin = a raw-data back office for founders (fix typos, bulk edits).
Everyday work happens in the app screens."""
from django.contrib import admin

from . import models as m

admin.site.site_header = "Studio Ops · back office"
for model in (m.Pod, m.Holiday, m.AgencyRate, m.DriveReturn, m.TaskEntry, m.PlanEntry, m.Review, m.InvoiceLine,
              m.PayrollMonth, m.Payslip, m.EmployeeOfMonth, m.Congrats, m.StudioSettings):
    admin.site.register(model)


@admin.register(m.Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "pod", "active")
    list_filter = ("role", "pod", "active")


@admin.register(m.Agency)
class AgencyAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "currency", "active")
    search_fields = ("name",)


@admin.register(m.Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("couple", "deliverable", "agency", "editor", "deadline", "status", "invoice_amount")
    list_filter = ("status", "agency", "deliverable", "is_extra")
    search_fields = ("couple",)


@admin.register(m.DayLog)
class DayLogAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "day_type", "time_in", "time_out")
    list_filter = ("day_type", "user")
    date_hierarchy = "date"


@admin.register(m.ExtraEntry)
class ExtraAdmin(admin.ModelAdmin):
    list_display = ("user", "project", "date", "start", "end")


@admin.register(m.HardDrive)
class DriveAdmin(admin.ModelAdmin):
    list_display = ("code", "agency", "status", "location")
    list_filter = ("status",)


@admin.register(m.Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "agency", "issued_on", "status", "received")
    list_filter = ("status", "agency")


# Back office is for founders only (superusers). Everyone else gets the app.
admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser
admin.site.site_header = "Studio Ops · Back office"
admin.site.site_title = "Studio Ops back office"


@admin.register(m.AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created", "username", "action", "target", "detail", "ip")
    list_filter = ("action",)
    search_fields = ("username", "action", "target", "detail")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
