"""Outbound parent messaging: what was sent, to whom, and what became of it.

Two models, both branch-owned so they inherit tenant scoping:

    Message  --<  MessageRecipient

``Message`` is one press of Send. ``MessageRecipient`` is one row per parent it
went to, and it is the reason this is a delivery log rather than a sent-items
folder: a bursar chasing school fees needs to know that Mrs. Okonkwo's number
bounced, not merely that "JSS 1A parents were messaged".

Nothing here is a parent account. Parents do not sign in; the platform's whole
relationship with them is these rows going out.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.core.models import BranchScopedModel

from .providers import Channel, DeliveryStatus


class AudienceType(models.TextChoices):
    """How the sender chose who to message.

    Stored alongside the human-readable description so the log can say *how* a
    batch was picked, and so "message the parents who owe" is one click from a
    message that was already sent that way.
    """

    ALL = "all", "All parents"
    CLASS = "class", "One class"
    STUDENTS = "students", "Selected students"
    OWING = "owing", "Parents who owe fees"


class MessageStatus(models.TextChoices):
    """The headline state of a whole batch.

    A rollup of the recipient rows, not an independent fact -- see
    :meth:`Message.refresh_status`.
    """

    QUEUED = "queued", "Queued"
    SENT = "sent", "Sent"
    PARTIAL = "partial", "Partly delivered"
    FAILED = "failed", "Failed"


#: Design-system pill class for each batch state. The four payment-status
#: colours are reused deliberately: staff have already learned that green means
#: fine and red means deal with this.
STATUS_PILLS = {
    MessageStatus.QUEUED: "status-unpaid",
    MessageStatus.SENT: "status-paid",
    MessageStatus.PARTIAL: "status-partial",
    MessageStatus.FAILED: "status-overdue",
}

RECIPIENT_PILLS = {
    DeliveryStatus.PENDING: "status-unpaid",
    DeliveryStatus.SENT: "status-partial",
    DeliveryStatus.DELIVERED: "status-paid",
    DeliveryStatus.FAILED: "status-overdue",
}


class Message(BranchScopedModel):
    """One send: a body, a channel, and the audience it went to."""

    branch = models.ForeignKey(
        "schools.Branch", on_delete=models.CASCADE, related_name="messages"
    )
    body = models.TextField(help_text="What the parent receives.")
    channel = models.CharField(
        max_length=20, choices=Channel.choices, default=Channel.SMS
    )
    audience = models.CharField(
        max_length=200,
        help_text='Human-readable, e.g. "JSS 1A parents" or "Parents who owe".',
    )
    audience_type = models.CharField(
        max_length=20, choices=AudienceType.choices, default=AudienceType.ALL
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # A message that was sent stays sent even if the person who sent it
        # leaves the school; the log must not lose the row with the account.
        on_delete=models.SET_NULL,
        related_name="messages_sent",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20, choices=MessageStatus.choices, default=MessageStatus.QUEUED
    )
    #: Which provider carried it, recorded at send time. A school that switches
    #: gateways mid-term needs to know which batch went out over which.
    provider = models.CharField(max_length=40, blank=True)

    class Meta(BranchScopedModel.Meta):
        abstract = False
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["school", "branch"]),
            models.Index(fields=["branch", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_channel_display()} to {self.audience}"

    def get_absolute_url(self) -> str:
        return reverse("messaging:message_detail", args=[self.pk])

    # -- delivery rollup -----------------------------------------------------

    @property
    def pill_class(self) -> str:
        return STATUS_PILLS.get(self.status, "status-unpaid")

    def delivery_counts(self) -> dict[str, int]:
        """How many recipients sit in each delivery state.

        One query. The list screen annotates instead (see
        :func:`apps.messaging.views.MessageListView`) so a page of messages is
        one query for the whole table rather than one per row.
        """
        counts = dict.fromkeys(DeliveryStatus.values, 0)
        rows = (
            self.recipients.values("status")
            .annotate(n=models.Count("id"))
            .order_by()
        )
        for row in rows:
            counts[row["status"]] = row["n"]
        return counts

    def refresh_status(self, *, save: bool = True) -> str:
        """Recompute :attr:`status` from the recipient rows.

        ``status`` is a rollup kept on the row so the log can be listed and
        filtered without a join, which makes it the one figure here that is
        stored rather than derived. It is therefore never *set* by hand: the
        dispatcher calls this after a batch, and a provider's delivery report
        will call it again days later when one recipient turns from sent to
        failed. The recipient rows remain the only source of truth.
        """
        counts = self.delivery_counts()
        total = sum(counts.values())
        failed = counts[DeliveryStatus.FAILED]
        succeeded = counts[DeliveryStatus.SENT] + counts[DeliveryStatus.DELIVERED]

        if total == 0 or counts[DeliveryStatus.PENDING] == total:
            status = MessageStatus.QUEUED
        elif failed == total:
            status = MessageStatus.FAILED
        elif failed or succeeded < total:
            status = MessageStatus.PARTIAL
        else:
            status = MessageStatus.SENT

        if status != self.status:
            self.status = status
            if save:
                # Straight through Model.save, not this class's, so the tenant
                # stamping in the base save() is not re-run on an existing row.
                super().save(update_fields=["status", "updated_at"])
        return self.status


class MessageRecipient(BranchScopedModel):
    """One parent's copy of a message, and what the provider did with it.

    The phone number is copied onto the row rather than read through the
    student. A parent who changes their number next term has not retroactively
    received last term's reminder on the new one, and a log that claims
    otherwise is worse than no log.
    """

    branch = models.ForeignKey(
        "schools.Branch", on_delete=models.CASCADE, related_name="message_recipients"
    )
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="recipients"
    )
    student = models.ForeignKey(
        "students.Student",
        # Keep the log intact if a student is removed from the roster: the
        # message was still sent, and the number it went to is on this row.
        on_delete=models.SET_NULL,
        related_name="message_recipients",
        null=True,
        blank=True,
        help_text="The student whose parent this went to.",
    )
    #: Snapshotted at send time, and the only number this row ever means.
    phone = models.CharField("phone number", max_length=20)
    #: Also snapshotted -- the log should still name the parent after the
    #: student row is gone.
    parent_name = models.CharField(max_length=160, blank=True)
    status = models.CharField(
        max_length=20, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING
    )
    provider_reference = models.CharField(max_length=120, blank=True)
    error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta(BranchScopedModel.Meta):
        abstract = False
        ordering = ["parent_name", "id"]
        constraints = [
            # One copy per student per message: pressing Send twice on a slow
            # connection must not text a parent twice.
            models.UniqueConstraint(
                fields=["message", "student"],
                name="one_recipient_row_per_student_per_message",
            )
        ]
        indexes = [
            models.Index(fields=["school", "branch"]),
            models.Index(fields=["message", "status"]),
            models.Index(fields=["student"]),
        ]

    def __str__(self) -> str:
        return f"{self.parent_name or self.phone} ({self.get_status_display()})"

    @property
    def pill_class(self) -> str:
        return RECIPIENT_PILLS.get(self.status, "status-unpaid")

    @property
    def phone_display(self) -> str:
        from apps.students.validators import format_phone

        return format_phone(self.phone)

    def save(self, *args, **kwargs):
        if self.message_id and self.branch_id is None:
            self.branch_id = self.message.branch_id
        super().save(*args, **kwargs)
