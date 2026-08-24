"""The messaging provider interface.

Everything above this file -- models, views, the dispatcher -- talks to
:class:`MessagingProvider` and never to a named vendor. Swapping Termii for
Africa's Talking is then a settings change, not a code change, which is the
whole point: the pilot has to work today on nothing but a log file, and the
credentials arrive whenever they arrive.

A provider does exactly one thing: take a number, a body and a channel, and
report back what happened. It does not touch the database, does not know what a
Student is, and does not raise on delivery failure -- a failed send is a
:class:`SendResult`, because one parent's disconnected line must not abort the
other thirty-one messages in the batch.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

from django.db import models


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


class ProviderNotConfigured(Exception):
    """Raised when a provider is selected but has no credentials.

    Surfaced to the user as "messaging is not configured" rather than as a
    stack trace: the fix is an environment variable, not a bug report.
    """


class MessagingProvider(abc.ABC):
    """One outbound channel to a parent's phone.

    Implementations are constructed once per dispatch and must be cheap to
    build -- credential checks belong in :meth:`check`, which the compose
    screen calls before it lets anyone press Send.
    """

    #: Short key used by ``settings.MESSAGING_PROVIDER``.
    key: str = ""
    #: Shown on screen when the school asks what is sending their messages.
    label: str = ""
    #: Channels this provider can actually carry.
    channels: tuple[str, ...] = (Channel.SMS,)

    @abc.abstractmethod
    def send(self, recipient: str, message: str, channel: str) -> SendResult:
        """Send ``message`` to ``recipient`` over ``channel``.

        ``recipient`` is a canonical local Nigerian number (``08031234567``);
        each provider converts to whatever shape its API wants. Must never
        raise for an ordinary delivery failure -- return
        :meth:`SendResult.failure` instead.
        """

    def check(self) -> None:
        """Raise :class:`ProviderNotConfigured` if this provider cannot send.

        The default provider can always send, so the base implementation does
        nothing.
        """

    @property
    def is_configured(self) -> bool:
        try:
            self.check()
        except ProviderNotConfigured:
            return False
        return True

    def supports(self, channel: str) -> bool:
        return channel in self.channels
