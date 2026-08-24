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
    MessagingProvider,
    ProviderNotConfigured,
    SendResult,
)


class AfricasTalkingProvider(MessagingProvider):
    key = "africastalking"
    label = "Africa's Talking"
    # Their WhatsApp product is a separate chat API with a different contract;
    # claiming it here would let a bursar pick a channel that cannot be carried.
    channels = (Channel.SMS,)

    def __init__(self):
        self.username = getattr(settings, "AFRICASTALKING_USERNAME", "")
        self.api_key = getattr(settings, "AFRICASTALKING_API_KEY", "")
        self.sender_id = getattr(settings, "MESSAGING_SENDER_ID", "")
        self.base_url = getattr(
            settings, "AFRICASTALKING_BASE_URL", "https://api.africastalking.com"
        )

    def check(self) -> None:
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
        body = {
            "username": self.username,
            "to": to_international(recipient),
            "message": message,
        }
        if self.sender_id:
            # Optional for them: without it the message goes out on a shared
            # short code rather than the school's name.
            body["from"] = self.sender_id
        return body

    def send(self, recipient: str, message: str, channel: str) -> SendResult:
        self.check()
        # TODO(credentials): POST self.payload(...) to
        # f"{self.base_url}/version1/messaging" with an apiKey header, and read
        # SMSMessageData.Recipients[0] for the status and messageId.
        return SendResult(
            status=DeliveryStatus.FAILED,
            error="Africa's Talking is selected but the integration is not live yet.",
        )
