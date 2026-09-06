"""Africa's Talking -- the pan-African alternative. Stub until credentials arrive.

Same shape as :mod:`.termii`: settings read, payload assembled, no HTTP call.
Africa's Talking takes numbers in full international form *with* the plus and
authenticates with a username as well as a key, which is why it reads two
settings rather than one.
"""

from __future__ import annotations

from django.conf import settings

from apps.students.validators import to_international

from .base import (
    Channel,
    DeliveryStatus,
    MessagePurpose,
    MessagingProvider,
    ProviderKey,
    ProviderNotConfigured,
    SendResult,
)


class AfricasTalkingProvider(MessagingProvider):
    key = ProviderKey.AFRICASTALKING
    label = "Africa's Talking"
    # Their WhatsApp product is a separate chat API with a different contract;
    # claiming it here would let a bursar pick a channel that cannot be carried.
    channels = (Channel.SMS,)

    def __init__(self, identity=None):
        super().__init__(identity)
        self.username = getattr(settings, "AFRICASTALKING_USERNAME", "")
        self.base_url = getattr(
            settings, "AFRICASTALKING_BASE_URL", "https://api.africastalking.com"
        )

    @property
    def api_key(self) -> str:
        """The school's own key if it has one, otherwise the platform's."""
        if self.identity is not None and self.identity.api_key:
            return self.identity.api_key
        return getattr(settings, "AFRICASTALKING_API_KEY", "")

    def check(self) -> None:
        super().check()
        missing = [
            name
            for name, value in (
                ("AFRICASTALKING_USERNAME", self.username),
                ("AFRICASTALKING_API_KEY", self.api_key),
            )
            if not value
        ]
        if missing:
            raise ProviderNotConfigured(
                f"{' and '.join(missing)} not set, so no message can be sent. "
                f"Set them in the environment, or run on the console provider."
            )

    def payload(self, recipient: str, message: str, channel: str) -> dict:
        return {
            "username": self.username,
            "to": to_international(recipient),
            "message": message,
            "from": self.sender_id,
        }

    def send(
        self,
        recipient: str,
        message: str,
        channel: str,
        *,
        purpose: str = MessagePurpose.TRANSACTIONAL,
    ) -> SendResult:
        self.check()
        # TODO(credentials): POST self.payload(...) to
        # f"{self.base_url}/version1/messaging" with an apiKey header, and read
        # SMSMessageData.Recipients[0] for the status and messageId. Africa's
        # Talking expresses the transactional/promotional split as a
        # bulkSMSMode flag and a sender-type on the account rather than as a
        # channel, so `purpose` maps onto that rather than onto Termii's
        # `channel` -- see TermiiProvider.termii_channel for the shape.
        return SendResult(
            status=DeliveryStatus.FAILED,
            error="Africa's Talking is selected but the integration is not live yet.",
        )
