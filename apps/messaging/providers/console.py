"""The provider the pilot runs on: log it and call it delivered.

Not a mock and not a test double -- it is the default, deliberately. The whole
messaging feature has to work end to end before anyone signs a contract with an
SMS gateway, and a school demo that dies on a missing API key demos nothing.
Every screen, every delivery log row and every status pill you see running on
this provider is the same code path a real gateway will drive.

It reports ``DELIVERED`` rather than ``SENT`` because there is no network in
between to be uncertain about: the message reached everything there is to
reach.
"""

from __future__ import annotations

import logging

from .base import Channel, DeliveryStatus, MessagingProvider, SendResult

logger = logging.getLogger("dependable.messaging")


class ConsoleProvider(MessagingProvider):
    key = "console"
    label = "Console (development)"
    channels = (Channel.SMS, Channel.WHATSAPP)

    def send(self, recipient: str, message: str, channel: str) -> SendResult:
        logger.info(
            "[%s] to %s: %s", Channel(channel).label, recipient, message
        )
        # A reference the log screen can show, shaped like a provider id so the
        # column is exercised honestly rather than sitting empty.
        return SendResult(
            status=DeliveryStatus.DELIVERED,
            reference=f"console-{abs(hash((recipient, message))) % 10**10:010d}",
        )
