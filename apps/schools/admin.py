from django.contrib import admin
from django.utils.html import format_html

from apps.core.admin import TenantScopedAdminMixin

from .models import Branch, School, SchoolStatus

_STATUS_COLOURS = {
    SchoolStatus.ACTIVE: "#0f7a52",
    SchoolStatus.TRIALING: "#1d3f79",
    SchoolStatus.PAST_DUE: "#a86a09",
    SchoolStatus.SUSPENDED: "#b32424",
    SchoolStatus.CANCELLED: "#5a6b85",
}


class BranchInline(admin.TabularInline):
    model = Branch
    extra = 0
    fields = ("name", "city", "state", "head", "is_active")
    autocomplete_fields = ("head",)
    show_change_link = True


@admin.register(School)
class SchoolAdmin(TenantScopedAdminMixin):
    tenant_school_field = "id"
    tenant_branch_field = None

    list_display = ("name", "plan", "status_badge", "branch_count", "user_count", "created_at")
    list_filter = ("plan", "status")
    search_fields = ("name", "slug", "contact_email")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    inlines = [BranchInline]
    fieldsets = (
        (None, {"fields": ("name", "slug")}),
        ("Subscription", {"fields": ("plan", "status")}),
        ("Contact", {"fields": ("contact_email", "contact_phone")}),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return format_html(
            '<span style="background:{}1a;color:{};padding:2px 8px;'
            'border-radius:8px;font-weight:600;font-size:12px">{}</span>',
            _STATUS_COLOURS.get(obj.status, "#5a6b85"),
            _STATUS_COLOURS.get(obj.status, "#5a6b85"),
            obj.get_status_display(),
        )

    @admin.display(description="Branches")
    def branch_count(self, obj):
        return obj.branches.count()

    @admin.display(description="Users")
    def user_count(self, obj):
        return obj.users.count()


@admin.register(Branch)
class BranchAdmin(TenantScopedAdminMixin):
    tenant_branch_field = "id"

    list_display = ("name", "school", "city", "state", "head", "is_active")
    list_filter = ("is_active", "school", "state")
    search_fields = ("name", "city", "state", "address", "school__name")
    autocomplete_fields = ("school", "head")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("school", "name", "is_active")}),
        ("Location", {"fields": ("city", "state", "address")}),
        ("Leadership", {"fields": ("head",)}),
        ("Audit", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )
