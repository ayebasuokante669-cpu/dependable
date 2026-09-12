"""Outbound parent messaging: who it came from, what was sent, and to whom.

Four models:

    SchoolMessagingConfig          -- the school's own sending identity
    WhatsAppTemplate               -- the WhatsApp messages it may start
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

from .providers import (
    PROVIDERS,
    Channel,
    DeliveryStatus,
    MessagePurpose,
    ProviderKey,
    TemplateMessage,
)
from .validators import (
    SENDER_ID_MAX_LENGTH,
    normalise_sender_id,
    template_variables,
    validate_sender_id,
    validate_template_body,
)


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
        # Termii, since they support one master account sending on behalf of
        # many schools under each school's own Sender ID -- the arrangement
        # this platform is built on, and the one BulkSMS Nigeria declined. A
        # school already registered with another gateway keeps it; this only
        # decides what a new registration starts as.
        default=ProviderKey.TERMII,
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

    # --- WhatsApp ------------------------------------------------------------
    # A second approval, held separately from the Sender ID's because it is
    # granted by somebody else (Meta, not the SMS gateway), on a different
    # timescale, and can be withdrawn independently. A school whose Sender ID is
    # approved may still be waiting on WhatsApp, and must still be able to send
    # SMS in the meantime.
    whatsapp_provider = models.CharField(
        "WhatsApp gateway",
        max_length=32,
        choices=ProviderKey.choices,
        blank=True,
        help_text="Blank means this school has not set up WhatsApp.",
    )
    whatsapp_device_id = models.CharField(
        "WhatsApp device ID",
        max_length=120,
        blank=True,
        help_text="The gateway's id for the school's connected WhatsApp "
        "Business number.",
    )
    whatsapp_status = models.CharField(
        "WhatsApp status",
        max_length=20,
        choices=SenderIdStatus.choices,
        default=SenderIdStatus.PENDING,
        help_text="Nothing sends over WhatsApp until Meta has approved the "
        "school's WhatsApp Business account.",
    )
    whatsapp_status_note = models.CharField(
        "WhatsApp note",
        max_length=250,
        blank=True,
        help_text="Why WhatsApp was rejected or suspended — shown to the school.",
    )
    whatsapp_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="whatsapp_identities_approved",
        null=True,
        blank=True,
    )
    whatsapp_approved_at = models.DateTimeField(null=True, blank=True)

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

    @property
    def is_whatsapp_set_up(self) -> bool:
        return bool(self.whatsapp_provider)

    @property
    def is_whatsapp_usable(self) -> bool:
        """Whether a WhatsApp message may go out for this school.

        The WhatsApp half of :attr:`is_usable`, and deliberately independent of
        it: approval of the Sender ID says nothing about approval by Meta.
        """
        return (
            self.is_whatsapp_set_up
            and self.whatsapp_status == SenderIdStatus.APPROVED
        )

    @property
    def whatsapp_state_label(self) -> str:
        """"Not set up", or the approval status. What the screens print."""
        if not self.is_whatsapp_set_up:
            return "Not set up"
        return self.get_whatsapp_status_display()

    @property
    def whatsapp_pill_class(self) -> str:
        if not self.is_whatsapp_set_up:
            return "status-unpaid"
        return SENDER_ID_PILLS.get(self.whatsapp_status, "status-unpaid")

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

    def approve_whatsapp(
        self, *, by=None, save: bool = True
    ) -> "SchoolMessagingConfig":
        """Mark the school's WhatsApp Business account approved by Meta."""
        self.whatsapp_status = SenderIdStatus.APPROVED
        self.whatsapp_status_note = ""
        self.whatsapp_approved_by = by
        self.whatsapp_approved_at = timezone.now()
        if save:
            self.save(
                update_fields=[
                    "whatsapp_status", "whatsapp_status_note",
                    "whatsapp_approved_by", "whatsapp_approved_at", "updated_at",
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

        # Each gateway field must name a gateway that carries its channel. One
        # list of providers feeds both dropdowns, and picking the WhatsApp
        # gateway for SMS would otherwise save cleanly and fail every batch.
        errors = {}
        if not _carries(self.provider, Channel.SMS):
            errors["provider"] = (
                f"{self.get_provider_display()} does not carry SMS. Choose the "
                f"gateway this Sender ID is registered with."
            )
        if self.whatsapp_provider and not _carries(
            self.whatsapp_provider, Channel.WHATSAPP
        ):
            errors["whatsapp_provider"] = (
                f"{self.get_whatsapp_provider_display()} does not carry WhatsApp."
            )
        provider_class = PROVIDERS.get(self.whatsapp_provider)
        if (
            self.whatsapp_status == SenderIdStatus.APPROVED
            and provider_class is not None
            and provider_class.requires_device_id
            and not self.whatsapp_device_id.strip()
        ):
            errors["whatsapp_device_id"] = (
                f"{provider_class.label} cannot send without the school's "
                f"WhatsApp device ID. Add it before approving."
            )
        if errors:
            raise ValidationError(errors)


def _carries(provider_key: str, channel: str) -> bool:
    provider_class = PROVIDERS.get(provider_key)
    return provider_class is not None and channel in provider_class.channels


class TemplateCategory(models.TextChoices):
    """Meta's category for a WhatsApp template.

    Meta decides it at review, prices by it, and polices it: a marketing
    message registered as utility gets the template paused. Authentication
    templates are left out -- they carry a one-time code and nothing else, and
    nothing a school sends from the compose screen is one.
    """

    UTILITY = "utility", "Utility"
    MARKETING = "marketing", "Marketing"


class WhatsAppTemplate(TenantScopedModel):
    """One WhatsApp message a school is allowed to start.

    WhatsApp Business does not let a business send free text to someone who
    has not messaged it first. Everything a school sends -- a fee reminder, a
    closure notice -- starts the conversation, so it has to be a template
    registered with the gateway and approved by Meta in advance, with only its
    variables filled in per parent.

    A row here *records* that registration; it does not create it. The
    template is written and submitted on the gateway's dashboard, against the
    school's WhatsApp device, and a platform administrator copies its id and
    its exact text here and marks it approved once Meta has.

    The body is kept, not just the id, for two reasons: the compose screen
    shows the sender what the parent will actually read, and its
    ``<%placeholders%>`` are checked against the variables the platform can
    fill -- a template asking for something nobody supplies is refused on every
    send, so it is refused here instead.

    Scoped per school, because templates belong to the school's own WhatsApp
    device.
    """

    name = models.CharField(
        max_length=80,
        help_text='What staff choose on the compose screen, e.g. "Fee reminder".',
    )
    provider = models.CharField(
        max_length=32,
        choices=ProviderKey.choices,
        default=ProviderKey.TERMII_WHATSAPP,
        help_text="The gateway this template is registered with.",
    )
    template_id = models.CharField(
        "template ID",
        max_length=120,
        help_text="The gateway's id for the template, from its dashboard.",
    )
    category = models.CharField(
        max_length=20,
        choices=TemplateCategory.choices,
        default=TemplateCategory.UTILITY,
    )
    language = models.CharField(max_length=10, default="en")
    body = models.TextField(
        validators=[validate_template_body],
        help_text="The approved text exactly as registered, with variables "
        "written <%parent_name%>. Available: <%parent_name%>, "
        "<%student_name%>, <%school_name%>, <%message%>.",
    )
    status = models.CharField(
        max_length=20,
        choices=SenderIdStatus.choices,
        default=SenderIdStatus.PENDING,
        help_text="Nothing sends with this template until Meta has approved it.",
    )
    status_note = models.CharField(
        max_length=250,
        blank=True,
        help_text="Why it was rejected or paused.",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantScopedModel.Meta):
        abstract = False
        verbose_name = "WhatsApp template"
        ordering = ["school__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "name"],
                name="one_whatsapp_template_name_per_school",
            ),
        ]
        indexes = [
            models.Index(fields=["school", "branch"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return self.name

    # -- derived facts -------------------------------------------------------

    @property
    def is_usable(self) -> bool:
        return self.status == SenderIdStatus.APPROVED and bool(self.template_id)

    @property
    def variables(self) -> list[str]:
        return template_variables(self.body)

    @property
    def purpose(self) -> str:
        """The stored ``Message.purpose`` a batch on this template gets.

        On WhatsApp the category, not the sender, says what a message is, so a
        batch is recorded as whatever its template was approved as.
        """
        if self.category == TemplateCategory.MARKETING:
            return MessagePurpose.PROMOTIONAL
        return MessagePurpose.TRANSACTIONAL

    @property
    def pill_class(self) -> str:
        return SENDER_ID_PILLS.get(self.status, "status-unpaid")

    def message_for(self, **values) -> TemplateMessage:
        """This template filled in for one recipient.

        Only the variables the body actually uses are sent; one the body uses
        and ``values`` lacks goes as an empty string rather than being dropped,
        so the parent reads a gap rather than WhatsApp refusing the message.
        """
        return TemplateMessage(
            template_id=self.template_id,
            data={name: str(values.get(name) or "") for name in self.variables},
            name=self.name,
            body=self.body,
        )

    # -- persistence ---------------------------------------------------------

    def save(self, *args, **kwargs):
        if self.status == SenderIdStatus.APPROVED:
            self.approved_at = self.approved_at or timezone.now()
        else:
            self.approved_at = None
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError

        if not _carries(self.provider, Channel.WHATSAPP):
            raise ValidationError(
                {"provider": f"{self.get_provider_display()} does not carry WhatsApp."}
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
    #: Transactional or promotional -- which gateway route this batch was
    #: allowed on. Stored rather than inferred at send time, because it is the
    #: answer to "why did this reach a DND number?" a year later, and because a
    #: batch resumed after a half-finished send must be routed the same way the
    #: first half was.
    #:
    #: Defaults to transactional, which is what essentially everything a school
    #: sends through this platform is: fee reminders, admission decisions,
    #: closure notices. Promotional is the deliberate exception, and the
    #: compose screen makes the sender say so.
    purpose = models.CharField(
        max_length=20,
        choices=MessagePurpose.choices,
        default=MessagePurpose.TRANSACTIONAL,
        help_text="Transactional messages use the gateway's DND route and "
        "reach parents who have Do-Not-Disturb on. Promotional messages do "
        "not, and are blocked overnight.",
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
    #: The approved template a WhatsApp batch went out as; empty for SMS.
    #: Protected rather than nulled on delete: a batch resumed later has to be
    #: sent as the same template, and a template that has carried messages is
    #: retired by changing its status, not by deleting the record of it.
    template = models.ForeignKey(
        WhatsAppTemplate,
        on_delete=models.PROTECT,
        related_name="messages",
        null=True,
        blank=True,
    )

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
