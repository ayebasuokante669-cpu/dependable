"""The messaging provider interface.

Everything above this file -- models, views, the dispatcher -- talks to
:class:`MessagingProvider` and never to a named vendor. Swapping BulkSMS
Nigeria for Termii is then a settings change, not a code change, which is the
whole point: the pilot has to work today on nothing but a log file, and the
credentials arrive whenever they arrive.

A provider does exactly one thing: take a number, a body and a channel, and
report back what happened. It does not touch the database, does not know what a
Student is, and does not raise on delivery failure -- a failed send is a
:class:`SendResult`, because one parent's disconnected line must not abort the
other thirty-one messages in the batch.

**Who the message comes from is not the provider's decision.** The platform
holds the gateway account; each school has its own registered Sender ID, and a
provider is handed one as a :class:`SenderIdentity` when it is built. A
provider constructed without one can carry nothing that needs a sender, and
says so through :meth:`check` rather than falling back to a blank or borrowed
name -- sending a school's fee reminder under somebody else's identity is worse
than not sending it.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

from django.db import models


class ProviderKey(models.TextChoices):
    """Every gateway the platform knows how to talk to.

    The single source of truth for provider identity: the registry in
    ``providers/__init__.py`` is keyed off these, each provider class carries
    one as its ``key``, and ``SchoolMessagingConfig.provider`` offers them as
    its choices. Adding a gateway means adding a member here and a class, and
    nothing else has a list to keep in step.
    """

    CONSOLE = "console", "Console (development)"
    BULKSMSNIGERIA = "bulksmsnigeria", "BulkSMS Nigeria"
    TERMII = "termii", "Termii"
    AFRICASTALKING = "africastalking", "Africa's Talking"


class Channel(models.TextChoices):
    """How a message reaches a parent."""

    SMS = "sms", "SMS"
    WHATSAPP = "whatsapp", "WhatsApp"


class DeliveryStatus(models.TextChoices):
    """Where one recipient's copy has got to.

    ``SENT`` and ``DELIVERED`` are deliberately different states: an SMS
    gateway accepting a message says nothing about whether the handset ever
    received it, and a school chasing a parent who "was messaged" needs to know
    which of the two actually happened.
    """

    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"


@dataclass(frozen=True)
class SendResult:
    """What a provider reports about one attempted send."""

    status: str = DeliveryStatus.PENDING
    #: The provider's own id for the message, quoted back in delivery reports.
    reference: str = ""
    #: Empty unless ``status`` is ``FAILED``; shown to the bursar verbatim.
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {DeliveryStatus.SENT, DeliveryStatus.DELIVERED}

    @classmethod
    def failure(cls, error: str) -> "SendResult":
        return cls(status=DeliveryStatus.FAILED, error=error)


@dataclass(frozen=True)
class SenderIdentity:
    """Who one school's messages come from.

    Resolved from that school's ``SchoolMessagingConfig`` and handed to the
    provider at construction. It is a plain value object on purpose: a provider
    must be able to send without touching the database, and a test must be able
    to state an identity without creating a school.

    ``api_key`` and ``account_reference`` are empty for the pilot, where the
    platform's master account sends on every school's behalf under the school's
    own Sender ID. A school that later takes out its own gateway account fills
    them in and nothing else changes.
    """

    sender_id: str
    provider_key: str = ProviderKey.CONSOLE
    #: The school's own gateway credential, when it has one. Empty means "use
    #: the platform master account".
    api_key: str = ""
    #: A sub-account or reseller reference on the master account, when the
    #: gateway supports one.
    account_reference: str = ""
    #: For logs and screens only -- never sent to a gateway.
    school_name: str = ""

    def __str__(self) -> str:
        return self.sender_id

    @property
    def uses_own_credentials(self) -> bool:
        return bool(self.api_key)


class ProviderNotConfigured(Exception):
    """Raised when a provider is selected but cannot send.

    Covers both halves of "configured": the platform's master credentials, and
    the school's own approved Sender ID. Surfaced to the user as a sentence
    naming the fix rather than as a stack trace -- it is an environment
    variable or an approval, not a bug report.
    """


class MessagingProvider(abc.ABC):
    """One outbound channel to a parent's phone, sending as one school.

    Implementations are constructed once per dispatch and must be cheap to
    build -- credential checks belong in :meth:`check`, which the compose
    screen calls before it lets anyone press Send.
    """

    #: Short key. One of :class:`ProviderKey`.
    key: str = ""
    #: Shown on screen when the school asks what is sending their messages.
    label: str = ""
    #: Channels this provider can actually carry.
    channels: tuple[str, ...] = (Channel.SMS,)
    #: Whether this provider needs a registered Sender ID to send at all. The
    #: console provider does not; every real gateway does.
    requires_sender_id: bool = True

    def __init__(self, identity: SenderIdentity | None = None):
        self.identity = identity

    @property
    def sender_id(self) -> str:
        """The name this provider will send under. Empty if it has no identity."""
        return self.identity.sender_id if self.identity else ""

    @abc.abstractmethod
    def send(self, recipient: str, message: str, channel: str) -> SendResult:
        """Send ``message`` to ``recipient`` over ``channel``.

        ``recipient`` is a canonical local Nigerian number (``08031234567``);
        each provider converts to whatever shape its API wants. The sender is
        not a parameter -- it is ``self.identity``, fixed when the provider was
        built, so a single dispatch physically cannot send half its batch under
        one school's name and half under another's.

        Must never raise for an ordinary delivery failure -- return
        :meth:`SendResult.failure` instead.
        """

    def check(self) -> None:
        """Raise :class:`ProviderNotConfigured` if this provider cannot send.

        Subclasses that need credentials override this and should call
        ``super().check()`` first, so the identity is verified before the
        vendor-specific keys are.
        """
        if self.requires_sender_id and not self.sender_id:
            raise ProviderNotConfigured(
                f"{self.label or 'This provider'} needs a registered Sender ID "
                f"and none was resolved for this school."
            )

    @property
    def is_configured(self) -> bool:
        try:
            self.check()
        except ProviderNotConfigured:
            return False
        return True

    def supports(self, channel: str) -> bool:
        return channel in self.channels
