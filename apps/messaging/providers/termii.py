"""Termii -- a Nigerian SMS/WhatsApp gateway. Stub until credentials arrive.

Everything except the HTTP call is real: the settings are read, the payload is
assembled, and :meth:`check` refuses to pretend it can send without an API key.
Filling this in is one method and one dependency, and nothing above it changes.

Termii wants numbers in international form without the plus (``2348031234567``)
and a registered sender ID; both are prepared below so the shape of the request
is settled before the account exists.
"""

from __future__ import annotations

from django.conf import settings

from apps.students.validators import to_international

from .base import (
    Channel,
    DeliveryStatus,
    MessagingProvider,
    ProviderNotConfigured,
    SendResult,
)


class TermiiProvider(MessagingProvider):
    key = "termii"
    label = "Termii"
    channels = (Channel.SMS, Channel.WHATSAPP)

    #: Termii's own name for each of our channels.
    CHANNEL_MAP = {Channel.SMS: "generic", Channel.WHATSAPP: "whatsapp"}

    def __init__(self):
        self.api_key = getattr(settings, "TERMII_API_KEY", "")
        self.sender_id = getattr(settings, "MESSAGING_SENDER_ID", "")
        self.base_url = getattr(
            settings, "TERMII_BASE_URL", "https://api.ng.termii.com"
        )

    def check(self) -> None:
        if not self.api_key:
            raise ProviderNotConfigured(
                "TERMII_API_KEY is not set, so no message can be sent. Set it in "
                "the environment, or run on the console provider."
            )
        if not self.sender_id:
            raise ProviderNotConfigured(
                "MESSAGING_SENDER_ID is not set. Termii will not accept a "
                "message without a registered sender ID."
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
