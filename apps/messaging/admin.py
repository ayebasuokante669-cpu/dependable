"""Admin for the delivery log.

Read-mostly on purpose: a message that has gone out cannot be unsent, so the
body, channel and audience are frozen once the batch exists. What support staff
actually need here is to look at why a number failed, which is why the recipient
rows are inline and their error text is on the list.
"""

from django.contrib import admin

from apps.core.admin import TenantScopedAdminMixin

from .models import (
    Message,
    MessageRecipient,
    SchoolMessagingConfig,
    SenderIdStatus,
)
from .providers import DeliveryStatus


class MessageRecipientInline(admin.TabularInline):
    model = MessageRecipient
    extra = 0
    can_delete = False
    fields = ("parent_name", "phone", "student", "status", "provider_reference",
              "error", "sent_at")
    readonly_fields = ("parent_name", "phone", "student", "provider_reference",
                       "sent_at")
    autocomplete_fields = ("student",)
    ordering = ("parent_name", "id")


@admin.register(Message)
class MessageAdmin(TenantScopedAdminMixin):
    list_display = ("audience", "channel", "branch", "sent_as", "sender",
                    "status", "delivery_summary", "created_at")
    list_filter = ("channel", "status", "audience_type", "branch")
    search_fields = ("audience", "body", "sender__username", "branch__name")
    autocomplete_fields = ("branch", "sender")
    readonly_fields = ("school", "provider", "sent_as", "created_at",
                       "updated_at")
    inlines = [MessageRecipientInline]
    fieldsets = (
        (None, {"fields": ("branch", "channel", "audience", "audience_type", "body")}),
        (
            "Delivery",
            {
                "fields": ("status", "provider", "sent_as", "sender"),
                "description": "Status is a rollup of the recipient rows below "
                "and is recomputed from them; it is not set by hand.",
            },
        ),
        ("Audit", {"fields": ("school", "created_at", "updated_at"),
                   "classes": ("collapse",)}),
    )

    @admin.display(description="Delivery")
    def delivery_summary(self, obj):
        counts = obj.delivery_counts()
        total = sum(counts.values())
        delivered = counts[DeliveryStatus.DELIVERED]
        failed = counts[DeliveryStatus.FAILED]
        return f"{delivered}/{total} delivered, {failed} failed"


@admin.register(MessageRecipient)
class MessageRecipientAdmin(TenantScopedAdminMixin):
    list_display = ("parent_name", "phone", "student", "message", "status",
                    "sent_at")
    list_filter = ("status", "branch", "message__channel")
    search_fields = ("parent_name", "phone", "provider_reference",
                     "student__last_name", "student__admission_number")
    autocomplete_fields = ("branch", "message", "student")
    readonly_fields = ("school", "created_at", "updated_at")


@admin.register(SchoolMessagingConfig)
class SchoolMessagingConfigAdmin(TenantScopedAdminMixin):
    """A school's Sender ID, for support work the screens do not cover.

    The approval trail is read-only: who approved a Sender ID and when is the
    record of what happened, and the way to change the status is the status
    field, which stamps them.
    """

    list_display = ("sender_id", "school", "scope_label", "provider", "status",
                    "uses_own_credentials", "approved_at")
    list_filter = ("status", "provider", "school")
    search_fields = ("sender_id", "school__name", "account_reference")
    autocomplete_fields = ("school", "branch", "approved_by")
    readonly_fields = ("approved_by", "approved_at", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("school", "branch", "sender_id", "provider")}),
        (
            "Approval",
            {
                "fields": ("status", "status_note"),
                "description": "Nothing sends under this Sender ID until it is "
                "approved. A rejection without a note tells the school nothing.",
            },
        ),
        (
            "Gateway account",
            {
                "fields": ("api_key", "account_reference"),
                "classes": ("collapse",),
                "description": "Blank means the platform's master account sends "
                "on this school's behalf, which is how the pilot works.",
            },
        ),
        ("Audit", {"fields": ("approved_by", "approved_at", "created_at",
                              "updated_at"),
                   "classes": ("collapse",)}),
    )

    @admin.display(description="Applies to")
    def scope_label(self, obj):
        return obj.scope_label

    @admin.display(description="Own account", boolean=True)
    def uses_own_credentials(self, obj):
        return obj.uses_own_credentials

    def save_model(self, request, obj, form, change):
        # Keep the admin and the screen stamping the trail the same way.
        if obj.status == SenderIdStatus.APPROVED and obj.approved_at is None:
            obj.approve(by=request.user, save=False)
        elif obj.status != SenderIdStatus.APPROVED:
            obj.approved_by = None
            obj.approved_at = None
        super().save_model(request, obj, form, change)
