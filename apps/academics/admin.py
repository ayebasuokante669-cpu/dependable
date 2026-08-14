from django.contrib import admin

from apps.core.admin import TenantScopedAdminMixin

from .models import Class, Subject


@admin.register(Class)
class ClassAdmin(TenantScopedAdminMixin):
    list_display = ("display_name", "branch", "get_level_display", "year_in_level",
                    "subject_count", "is_active")
    list_filter = ("level", "is_active", "branch")
    search_fields = ("name", "stream", "branch__name")
    autocomplete_fields = ("branch",)
    readonly_fields = ("school", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("branch", "name", "level", "year_in_level", "stream")}),
        ("Status", {"fields": ("is_active",)}),
        (
            "Audit",
            {
                "fields": ("school", "created_at", "updated_at"),
                "classes": ("collapse",),
                "description": "School is derived from the branch.",
            },
        ),
    )

    @admin.display(description="Class", ordering="name")
    def display_name(self, obj):
        return obj.display_name

    @admin.display(description="Subjects")
    def subject_count(self, obj):
        return obj.subjects.count()


@admin.register(Subject)
class SubjectAdmin(TenantScopedAdminMixin):
    list_display = ("name", "code", "branch", "class_count", "is_active")
    list_filter = ("is_active", "branch")
    search_fields = ("name", "code", "branch__name")
    autocomplete_fields = ("branch",)
    filter_horizontal = ("classes",)
    readonly_fields = ("school", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("branch", "name", "code", "classes")}),
        ("Status", {"fields": ("is_active",)}),
        (
            "Audit",
            {
                "fields": ("school", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Classes")
    def class_count(self, obj):
        return obj.classes.count()

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "classes":
            # Scoped manager: never offer another tenant's classes.
            kwargs.setdefault("queryset", Class.objects.all())
        return super().formfield_for_manytomany(db_field, request, **kwargs)
