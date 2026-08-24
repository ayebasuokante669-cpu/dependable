"""Outbound parent messaging: who it came from, what was sent, and to whom.

Three models:

    SchoolMessagingConfig          -- the school's own sending identity
    Message  --<  MessageRecipient -- what went out under it

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
from django.utils import timezone

from apps.core.models import BranchScopedModel, TenantScopedModel

from .providers import Channel, DeliveryStatus, ProviderKey
from .validators import SENDER_ID_MAX_LENGTH, normalise_sender_id, validate_sender_id


class SenderIdStatus(models.TextChoices):
    """Where a school's Sender ID stands with the gateway.

    Registration is not instant and is not guaranteed: a gateway reviews an
    alphanumeric sender before it will carry anything under it, and does reject
    them. Modelling that as a status rather than a boolean means the screen can
    tell a proprietor *why* their school is not sending yet, which is the only
    version of this a school can act on.
    """

    PENDING = "pending", "Awaiting approval"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    SUSPENDED = "suspended", "Suspended"


#: Design-system pill per status, reusing the four payment-status colours the
#: rest of the platform has already taught staff to read.
SENDER_ID_PILLS = {
    SenderIdStatus.PENDING: "status-partial",
    SenderIdStatus.APPROVED: "status-paid",
    SenderIdStatus.REJECTED: "status-overdue",
    SenderIdStatus.SUSPENDED: "status-overdue",
}


class SchoolMessagingConfig(TenantScopedModel):
    """One school's messaging identity: the name its parents see.

    The platform holds the gateway account and pays for the units; each school
    registers its own alphanumeric Sender ID against it. So SCHOOLCORD sends,
    and "Dapgroup" is what arrives on the handset.

    Scoped per school, with ``branch`` left nullable and part of the uniqueness
    rule so a school that later wants one Sender ID per campus can have it
    without a redesign: a row with no branch is the school's default, and a row
    with one overrides it for that campus. :func:`apps.messaging.identity.resolve`
    is the only place that ordering is expressed.

    Credentials are nullable on purpose. For the pilot every school falls
    through to the platform's master account and is distinguished only by its
    Sender ID; a school that later takes out its own gateway account fills in
    ``api_key`` and nothing else about the send path changes.
    """

    sender_id = models.CharField(
        "Sender ID",
        max_length=SENDER_ID_MAX_LENGTH,
        validators=[validate_sender_id],
        help_text=(
            'The name parents see the message come from, e.g. "Dapgroup". '
            f"At most {SENDER_ID_MAX_LENGTH} characters, and it must be "
            f"registered with the gateway before it will carry anything."
        ),
    )
    provider = models.CharField(
        max_length=32,
        choices=ProviderKey.choices,
        default=ProviderKey.BULKSMSNIGERIA,
        help_text="The gateway this Sender ID is registered with.",
    )
    status = models.CharField(
        max_length=20,
        choices=SenderIdStatus.choices,
        default=SenderIdStatus.PENDING,
        help_text="Nothing sends under this Sender ID until it is approved.",
    )
    status_note = models.CharField(
        max_length=250,
        blank=True,
        help_text="Why it was rejected or suspended — shown to the school.",
    )

    # --- The school's own gateway account, if it ever has one -------------
    api_key = models.CharField(
        "provider API key",
        max_length=255,
        blank=True,
        help_text="Leave blank to send on the platform's master account, which "
        "is how the pilot works.",
    )
    account_reference = models.CharField(
        max_length=120,
        blank=True,
        help_text="Sub-account or reseller reference on the master account, "
        "if the gateway issues one.",
    )

    # --- Approval trail ----------------------------------------------------
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="sender_ids_approved",
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantScopedModel.Meta):
        abstract = False
        verbose_name = "messaging identity"
        verbose_name_plural = "messaging identities"
        ordering = ["school__name", "branch__name"]
        constraints = [
            # One identity per campus...
            models.UniqueConstraint(
                fields=["school", "branch"],
                name="one_messaging_config_per_branch",
            ),
            # ...and one school-wide default. A second constraint is needed
            # because SQL treats NULLs as distinct, so the rule above would
            # happily allow a school two school-wide rows and leave resolution
            # picking whichever came back first.
            models.UniqueConstraint(
                fields=["school"],
                condition=models.Q(branch__isnull=True),
                name="one_default_messaging_config_per_school",
            ),
        ]
        indexes = [
            models.Index(fields=["school", "branch"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.sender_id} ({self.get_provider_display()})"

    def get_absolute_url(self) -> str:
        return reverse("messaging:identity")

    # -- derived facts -------------------------------------------------------

    @property
    def is_approved(self) -> bool:
        return self.status == SenderIdStatus.APPROVED

    @property
    def is_usable(self) -> bool:
        """Whether a message may actually go out under this identity.

        The single rule the send path asks about. Deliberately not the same
        thing as "a row exists": an unapproved Sender ID is a gateway rejection
        waiting to happen, and the platform should refuse first and say why.
        """
        return self.is_approved and bool(self.sender_id)

    @property
    def pill_class(self) -> str:
        return SENDER_ID_PILLS.get(self.status, "status-unpaid")

    @property
    def scope_label(self) -> str:
        return self.branch.name if self.branch_id else "All campuses"

    @property
    def uses_own_credentials(self) -> bool:
        return bool(self.api_key)

    # -- transitions ---------------------------------------------------------

    def approve(self, *, by=None, save: bool = True) -> "SchoolMessagingConfig":
        """Mark the Sender ID registered and usable."""
        self.status = SenderIdStatus.APPROVED
        self.status_note = ""
        self.approved_by = by
        self.approved_at = timezone.now()
        if save:
            self.save(
                update_fields=[
                    "status", "status_note", "approved_by", "approved_at",
                    "updated_at",
                ]
            )
        return self

    # -- persistence ---------------------------------------------------------

    def save(self, *args, **kwargs):
        # A platform owner has no school of their own, so the branch -- which
        # always knows its school -- is what places the config.
        if self.branch_id and self.school_id is None:
            self.school_id = self.branch.school_id
        self.sender_id = normalise_sender_id(self.sender_id)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError

        self.sender_id = normalise_sender_id(self.sender_id)
        if self.branch_id and self.school_id:
            if self.branch.school_id != self.school_id:
                raise ValidationError(
                    {"branch": "That campus belongs to a different school."}
                )


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
    #: The Sender ID this batch went out under, snapshotted like the recipients'
    #: phone numbers are. A school that renames its Sender ID next term has not
    #: retroactively sent last term's reminders under the new name, and a log
    #: that claims otherwise is worse than no log.
    #:
    #: Named ``sent_as`` rather than ``sender_id`` because ``sender`` above is a
    #: foreign key, and Django already owns that column name for its id.
    sent_as = models.CharField("Sender ID", max_length=32, blank=True)

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
