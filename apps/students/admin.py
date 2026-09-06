from django.contrib import admin

from apps.core.admin import TenantScopedAdminMixin

from .models import Student


@admin.register(Student)
class StudentAdmin(TenantScopedAdminMixin):
    list_display = (
        "admission_number",
        "formal_name",
        "school_class",
        "status",
        "parent_name",
        "parent_phone",
        "branch",
    )
    list_filter = ("status", "sex", "branch", "school_class__level", "school_class")
    search_fields = (
        "admission_number",
        "first_name",
        "last_name",
        "other_names",
        "parent_name",
        "parent_phone",
    )
    ordering = ("last_name", "first_name")
    autocomplete_fields = ("school_class",)
    readonly_fields = ("school", "branch", "created_at", "updated_at")
    date_hierarchy = "date_admitted"
    fieldsets = (
        (
            "Identity",
            {
                "fields": (
                    "admission_number",
                    ("first_name", "last_name"),
                    "other_names",
                    ("sex", "date_of_birth"),
                )
            },
        ),
        ("Enrolment", {"fields": ("school_class", "date_admitted", "status")}),
        (
            "Parent / guardian",
            {"fields": ("parent_name", "parent_phone", "parent_email", "address")},
        ),
        (
            "Audit",
            {
                "fields": ("school", "branch", "created_at", "updated_at"),
                "classes": ("collapse",),
                "description": "Branch and school are derived from the class.",
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("school_class", "branch")

    @admin.display(description="Name", ordering="last_name")
    def formal_name(self, obj):
        return obj.formal_name
