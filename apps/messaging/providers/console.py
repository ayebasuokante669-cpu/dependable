"""The provider the pilot runs on: log it and call it delivered.

Not a mock and not a test double -- it is the default, deliberately. The whole
messaging feature has to work end to end before anyone signs a contract with an
SMS gateway, and a school demo that dies on a missing API key demos nothing.
Every screen, every delivery log row and every status pill you see running on
this provider is the same code path a real gateway will drive.

It reports ``DELIVERED`` rather than ``SENT`` because there is no network in
between to be uncertain about: the message reached everything there is to
reach.

What it logs is the point of it. The line carries the Sender ID the school
would have sent under and the gateway that would have carried it, so per-tenant
messaging identity is verifiable -- in a test, in a terminal, in a demo -- with
no credentials and no money spent.
"""

from __future__ import annotations

import logging

from .base import (
    Channel,
    DeliveryStatus,
    MessagingProvider,
    ProviderKey,
    SendResult,
)

logger = logging.getLogger("dependable.messaging")


class ConsoleProvider(MessagingProvider):
    key = ProviderKey.CONSOLE
    label = "Console (development)"
    channels = (Channel.SMS, Channel.WHATSAPP)
    # The one provider that can carry a message without a registered Sender ID:
    # there is nobody on the other end to reject it. It still reports what the
    # identity was, or that there was none, so a school with no approved Sender
    # ID is visible here rather than silently fine.
    requires_sender_id = False

    def send(self, recipient: str, message: str, channel: str) -> SendResult:
        logger.info(
            "[%s] from %s via %s to %s: %s",
            Channel(channel).label,
            self.describe_sender(),
            self.describe_gateway(),
            recipient,
            message,
        )
        # A reference the log screen can show, shaped like a provider id so the
        # column is exercised honestly rather than sitting empty.
        return SendResult(
            status=DeliveryStatus.DELIVERED,
            reference=f"console-{abs(hash((recipient, message))) % 10**10:010d}",
        )

    # -- what it would have been --------------------------------------------

    def describe_sender(self) -> str:
        """The Sender ID this message would have gone out under."""
        if not self.identity:
            return "(no Sender ID resolved)"
        school = self.identity.school_name
        return f"{self.identity.sender_id}{f' [{school}]' if school else ''}"

    def describe_gateway(self) -> str:
        """The gateway that would have carried it, had this not been the console.

        Reads the school's own configured provider rather than this class, so
        the log says "BulkSMS Nigeria" for a school registered with them even
        while nothing leaves the machine.
        """
        if not self.identity:
            return self.label
        try:
            return ProviderKey(self.identity.provider_key).label
        except ValueError:
            return self.identity.provider_key or self.label
