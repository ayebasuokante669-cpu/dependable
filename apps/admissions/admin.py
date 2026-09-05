"""Admin for admissions.

The screens in this app cover the pipeline a school works every day. The admin
covers the two things the screens deliberately do not: editing an intake fee
sheet line by line -- which is seeded from
:mod:`apps.admissions.admission_pricing` and only occasionally corrected -- and
configuring a requirement set, which a school does once a year at most.

Audit fields are read-only throughout, for the reason the payments admin gives:
who decided, when they decided and who the applicant became are the record of
what happened, not fields to be tidied up.
"""

from django.contrib import admin

from apps.core.admin import TenantScopedAdminMixin

from .models import (
    AdmissionFeeItem,
    AdmissionFeeSchedule,
    AdmissionPayment,
    AdmissionsConfig,
    Applicant,
    Assessment,
    Requirement,
    RequirementSet,
)


class RequirementInline(admin.TabularInline):
    model = Requirement
    extra = 1
    fields = ("kind", "is_required", "position")
    # school/branch are filled in from the parent set on save.
    exclude = ("school", "branch")


@admin.register(RequirementSet)
class RequirementSetAdmin(TenantScopedAdminMixin):
    list_display = ("__str__", "school", "branch", "level", "is_active")
    list_filter = ("level", "is_active", "branch")
    autocomplete_fields = ("branch",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [RequirementInline]
    fieldsets = (
        (
            None,
            {
                "fields": ("school", "branch", "level", "is_active", "note"),
                "description": "Leave the campus blank for a policy that "
                "covers the whole school. A set with a campus overrides the "
                "school-wide one for that campus only. A level with no set at "
                "all runs on the platform defaults.",
            },
        ),
        ("Trail", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(AdmissionsConfig)
class AdmissionsConfigAdmin(TenantScopedAdminMixin):
    list_display = (
        "school",
        "enquiry_validity_days",
        "reminder_after_days",
        "bursar_can_view_applicants",
        "require_payment_before_enrolment",
    )
    list_filter = ("bursar_can_view_applicants", "require_payment_before_enrolment")
    readonly_fields = ("created_at", "updated_at")
    tenant_branch_field = None


class AdmissionFeeItemInline(admin.TabularInline):
    model = AdmissionFeeItem
    extra = 1
    fields = ("kind", "name", "amount", "position")
    exclude = ("school", "branch")


@admin.register(AdmissionFeeSchedule)
class AdmissionFeeScheduleAdmin(TenantScopedAdminMixin):
    list_display = (
        "school_class",
        "academic_year",
        "branch",
        "admission_total",
        "compulsory_total",
        "books_total",
        "total",
    )
    list_filter = ("academic_year", "is_active", "branch")
    search_fields = ("school_class__name", "school_class__stream")
    autocomplete_fields = ("branch", "school_class")
    readonly_fields = ("school", "created_at", "updated_at")
    inlines = [AdmissionFeeItemInline]
    fieldsets = (
        (
            None,
            {
                "fields": ("school_class", "branch", "academic_year", "is_active",
                           "note"),
                "description": "Charged once, at intake. Entirely separate "
                "from the termly fee structure — editing here never touches "
                "what a class owes per term.",
            },
        ),
        (
            "Trail",
            {"fields": ("school", "created_at", "updated_at"),
             "classes": ("collapse",)},
        ),
    )

    def get_queryset(self, request):
        # Every list column is a total over the line items.
        return super().get_queryset(request).prefetch_related("items")


@admin.register(AdmissionPayment)
class AdmissionPaymentAdmin(TenantScopedAdminMixin):
    list_display = ("applicant", "amount", "method", "status", "received_on", "branch")
    list_filter = ("status", "method", "branch")
    search_fields = (
        "applicant__first_name",
        "applicant__last_name",
        "applicant__reference",
        "reference",
    )
    autocomplete_fields = ("branch", "applicant", "recorded_by")
    date_hierarchy = "received_on"
    readonly_fields = ("school", "created_at", "updated_at")


class AssessmentInline(admin.StackedInline):
    model = Assessment
    extra = 0
    fields = ("scheduled_for", "sat_on", "score", "max_score", "outcome", "notes")
    exclude = ("school", "branch")


@admin.register(Applicant)
class ApplicantAdmin(TenantScopedAdminMixin):
    list_display = (
        "reference",
        "full_name",
        "status",
        "applying_for",
        "branch",
        "parent_name",
        "expires_at",
    )
    list_filter = ("status", "source", "level", "heard_about", "branch")
    search_fields = (
        "reference",
        "first_name",
        "last_name",
        "other_names",
        "parent_name",
        "parent_phone",
        "parent_email",
    )
    autocomplete_fields = ("branch", "school_class", "student", "decided_by")
    date_hierarchy = "created_at"
    inlines = [AssessmentInline]
    readonly_fields = (
        "school",
        "reference",
        "applied_at",
        "decided_at",
        "decision_sent_at",
        "reminder_sent_at",
        "enrolled_at",
        "expired_at",
        "withdrawn_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "Enquiry",
            {
                "fields": (
                    "reference", "status", "source", "branch", "level",
                    "school_class", "heard_about",
                ),
            },
        ),
        (
            "Child",
            {"fields": ("first_name", "last_name", "other_names", "sex",
                        "date_of_birth", "previous_school")},
        ),
        (
            "Parent / guardian",
            {"fields": ("parent_name", "parent_phone", "parent_email",
                        "parent_occupation", "address")},
        ),
        (
            "Application",
            {
                "fields": ("applied_at", "nationality", "state_of_origin", "notes",
                           "passport_photo", "birth_certificate",
                           "previous_results", "transfer_letter",
                           "immunisation_record"),
                "description": "Added when the enquiry becomes an application. "
                "Which of these are compulsory depends on the level — see "
                "Admission requirement sets.",
            },
        ),
        (
            "Validity",
            {
                "fields": ("expires_at", "reminder_sent_at"),
                "description": "An enquiry that has not become an application "
                "by the expiry date is closed by the expire_enquiries command.",
            },
        ),
        (
            "Decision",
            {"fields": ("decided_at", "decided_by", "decision_note",
                        "decision_sent_at")},
        ),
        (
            "Outcome",
            {"fields": ("student", "enrolled_at", "expired_at", "withdrawn_at",
                        "closing_note")},
        ),
        (
            "Trail",
            {"fields": ("school", "created_at", "updated_at"),
             "classes": ("collapse",)},
        ),
    )
