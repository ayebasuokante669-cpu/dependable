"""Termii -- a Nigerian SMS/WhatsApp gateway. Stub until credentials arrive.

Everything except the HTTP call is real: the settings are read, the payload is
assembled, and :meth:`check` refuses to pretend it can send without an API key.
Filling this in is one method and one dependency, and nothing above it changes.

Termii wants numbers in international form without the plus (``2348031234567``)
and a registered sender ID; both are prepared below so the shape of the request
is settled before the account exists. The sender comes from the school's
:class:`SenderIdentity`, not from a platform-wide setting -- Termii registers
sender IDs per account exactly as BulkSMS Nigeria does.
"""

from __future__ import annotations

from django.conf import settings

from apps.students.validators import to_international

from .base import (
    Channel,
    DeliveryStatus,
    MessagingProvider,
    ProviderKey,
    ProviderNotConfigured,
    SendResult,
)


class TermiiProvider(MessagingProvider):
    key = ProviderKey.TERMII
    label = "Termii"
    channels = (Channel.SMS, Channel.WHATSAPP)

    #: Termii's own name for each of our channels.
    CHANNEL_MAP = {Channel.SMS: "generic", Channel.WHATSAPP: "whatsapp"}

    def __init__(self, identity=None):
        super().__init__(identity)
        self.base_url = getattr(
            settings, "TERMII_BASE_URL", "https://api.ng.termii.com"
        )

    @property
    def api_key(self) -> str:
        """The school's own key if it has one, otherwise the platform's."""
        if self.identity is not None and self.identity.api_key:
            return self.identity.api_key
        return getattr(settings, "TERMII_API_KEY", "")

    def check(self) -> None:
        super().check()
        if not self.api_key:
            raise ProviderNotConfigured(
                "TERMII_API_KEY is not set, so no message can be sent. Set it in "
                "the environment, or run on the console provider."
            )

    def payload(self, recipient: str, message: str, channel: str) -> dict:
        """The request body, built and testable without an account."""
        return {
            "to": to_international(recipient, plus=False),
            "from": self.sender_id,
            "sms": message,
            "type": "plain",
            "channel": self.CHANNEL_MAP.get(channel, "generic"),
            "api_key": self.api_key,
        }

    def send(self, recipient: str, message: str, channel: str) -> SendResult:
        self.check()
        # TODO(credentials): POST self.payload(...) to
        # f"{self.base_url}/api/sms/send" and map the response's message_id onto
        # SendResult.reference. Termii accepting a message means SENT, not
        # DELIVERED -- delivery arrives later on their status webhook.
        return SendResult(
            status=DeliveryStatus.FAILED,
            error="Termii is selected but the integration is not live yet.",
        )
