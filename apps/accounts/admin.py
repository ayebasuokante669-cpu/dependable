from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.core.admin import TenantScopedAdminMixin

from .models import User


@admin.register(User)
class UserAdmin(TenantScopedAdminMixin, DjangoUserAdmin):
    """Standard Django user admin plus the tenancy and role fields."""

    list_display = (
        "username", "get_full_name", "email", "school", "branch", "role",
        "job_title", "is_active",
    )
    list_filter = ("role", "school", "branch", "is_active", "is_staff", "is_superuser")
    search_fields = ("username", "first_name", "last_name", "email", "job_title")
    autocomplete_fields = ("school", "branch")
    ordering = ("last_name", "first_name", "username")

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email", "phone")}),
        (
            "Tenancy & access",
            {
                "fields": ("school", "branch", "role", "job_title"),
                "description": (
                    "<b>Permission role</b> controls what this account can see. "
                    "<b>Job title</b> is descriptive only and grants nothing."
                ),
            },
        ),
        (
            "Django permissions",
            {
                "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
                "classes": ("collapse",),
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined"), "classes": ("collapse",)}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username", "email", "password1", "password2",
                    "school", "branch", "role", "job_title",
                ),
            },
        ),
    )

    @admin.display(description="Name", ordering="last_name")
    def get_full_name(self, obj):
        return obj.get_full_name()
