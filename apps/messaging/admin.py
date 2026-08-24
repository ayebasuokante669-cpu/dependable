"""Admin for the delivery log.

Read-mostly on purpose: a message that has gone out cannot be unsent, so the
body, channel and audience are frozen once the batch exists. What support staff
actually need here is to look at why a number failed, which is why the recipient
rows are inline and their error text is on the list.
"""

from django.contrib import admin

from apps.core.admin import TenantScopedAdminMixin

from .models import Message, MessageRecipient
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
    list_display = ("audience", "channel", "branch", "sender", "status",
                    "delivery_summary", "created_at")
    list_filter = ("channel", "status", "audience_type", "branch")
    search_fields = ("audience", "body", "sender__username", "branch__name")
    autocomplete_fields = ("branch", "sender")
    readonly_fields = ("school", "provider", "created_at", "updated_at")
    inlines = [MessageRecipientInline]
    fieldsets = (
        (None, {"fields": ("branch", "channel", "audience", "audience_type", "body")}),
        (
            "Delivery",
            {
                "fields": ("status", "provider", "sender"),
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
