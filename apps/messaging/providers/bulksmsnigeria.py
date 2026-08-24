"""BulkSMS Nigeria -- the pilot's real gateway.

The account model this implements is the one the platform actually has:
SCHOOLCORD holds a single master account with BulkSMS Nigeria and pays for the
units; each school registers its own alphanumeric Sender ID against that
account. So the API token comes from the environment and the ``from`` on every
request comes from the school's :class:`SenderIdentity`. A school that later
takes out its own account supplies its own token on its config and the same
code sends with it instead.

Talks HTTP with ``urllib`` from the standard library rather than pulling in
``requests``. One POST with a JSON body and a bearer token does not justify a
dependency the school's server then has to keep patched, and ``urllib`` gives
us the timeout, which is the part that actually matters when a gateway is slow.

BulkSMS Nigeria accepting a message means ``SENT``, never ``DELIVERED``: their
response says the message entered the queue, and whether a handset received it
arrives later on their delivery-report webhook. Reporting it as delivered would
make the log lie about the one thing a bursar chasing a parent needs it for.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

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

logger = logging.getLogger("dependable.messaging")

#: How long to wait on one send before giving up on it. A batch is sent in a
#: request cycle, so a gateway that has stopped answering must fail that
#: recipient quickly rather than hold the bursar's browser open.
TIMEOUT_SECONDS = 15

#: BulkSMS Nigeria's DND routing flag.
#:   0 -- normal route, blocked for numbers on the DND register
#:   1 -- DND route
#:   2 -- route via the corporate/DND-enabled path, retrying on the normal one
#: School fee reminders have to reach parents who have switched DND on, which
#: most Nigerian subscribers have, so 2 is the default and the only sane one.
DEFAULT_DND = "2"


class BulkSMSNigeriaProvider(MessagingProvider):
    key = ProviderKey.BULKSMSNIGERIA
    label = "BulkSMS Nigeria"
    # SMS only. They resell a WhatsApp product through a different API with a
    # different contract; claiming it here would let a bursar pick a channel
    # this class cannot carry.
    channels = (Channel.SMS,)

    def __init__(self, identity=None):
        super().__init__(identity)
        self.base_url = getattr(
            settings, "BULKSMSNIGERIA_BASE_URL", "https://www.bulksmsnigeria.com"
        ).rstrip("/")
        self.dnd = str(getattr(settings, "BULKSMSNIGERIA_DND", DEFAULT_DND))

    # -- credentials --------------------------------------------------------

    @property
    def api_token(self) -> str:
        """The school's own token if it has one, otherwise the platform's.

        This is the whole "master account, per-school identity" arrangement in
        one property: for the pilot every school falls through to the platform
        token and is distinguished only by its Sender ID.
        """
        if self.identity is not None and self.identity.api_key:
            return self.identity.api_key
        return getattr(settings, "BULKSMSNIGERIA_API_TOKEN", "")

    def check(self) -> None:
        # Identity first: a missing Sender ID is the school's problem to fix
        # and a missing token is the platform's, and saying so in that order
        # gets the message to the right person.
        super().check()
        if not self.api_token:
            raise ProviderNotConfigured(
                "BULKSMSNIGERIA_API_TOKEN is not set, so no message can be "
                "sent. Set the platform's master token in the environment, or "
                "run on the console provider."
            )

    # -- the request --------------------------------------------------------

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/api/v2/sms"

    def payload(self, recipient: str, message: str) -> dict:
        """The request body. Built and testable without an account."""
        return {
            "from": self.sender_id,
            "to": to_international(recipient, plus=False),
            "body": message,
            "dnd": self.dnd,
        }

    def send(self, recipient: str, message: str, channel: str) -> SendResult:
        self.check()
        payload = self.payload(recipient, message)

        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8", errors="replace")
            return self.read_response(body)
        except urllib.error.HTTPError as exc:
            # A 4xx carries the gateway's own explanation of what is wrong with
            # the message -- an unregistered sender, no units left -- which is
            # far more use to the bursar than "HTTP 422".
            detail = exc.read().decode("utf-8", errors="replace")
            return SendResult.failure(self.read_error(exc.code, detail))
        except urllib.error.URLError as exc:
            return SendResult.failure(
                f"Could not reach BulkSMS Nigeria: {exc.reason}."
            )
        except TimeoutError:
            return SendResult.failure(
                f"BulkSMS Nigeria did not answer within {TIMEOUT_SECONDS} seconds."
            )

    # -- their answers ------------------------------------------------------

    def read_response(self, body: str) -> SendResult:
        """Turn a 2xx body into a result.

        Their API answers 200 for messages it then refuses, with the refusal in
        ``error``, so a 2xx is not on its own a success.
        """
        try:
            parsed = json.loads(body or "{}")
        except ValueError:
            return SendResult.failure(
                "BulkSMS Nigeria returned something that was not JSON."
            )

        error = parsed.get("error")
        if error:
            return SendResult.failure(_flatten(error))

        data = parsed.get("data") or parsed
        status = str(data.get("status", "")).lower()
        reference = str(
            data.get("message_id") or data.get("id") or data.get("reference") or ""
        )

        if status and status not in {"success", "ok", "sent", "queued"}:
            return SendResult.failure(
                _flatten(data.get("message") or data) or f"Gateway status: {status}."
            )

        logger.info(
            "[BulkSMS Nigeria] %s -> %s (ref %s)",
            self.sender_id, _mask(_recipient_of(data)), reference or "none",
        )
        # Accepted into their queue. Delivery is a later fact, reported by
        # their webhook -- see the module docstring.
        return SendResult(status=DeliveryStatus.SENT, reference=reference)

    def read_error(self, code: int, body: str) -> str:
        try:
            parsed = json.loads(body or "{}")
        except ValueError:
            parsed = {}
        detail = _flatten(parsed.get("error") or parsed.get("message") or "")

        if code in (401, 403):
            return (
                detail
                or "BulkSMS Nigeria rejected the account credentials (HTTP "
                f"{code}). Check BULKSMSNIGERIA_API_TOKEN."
            )
        if code == 422:
            return (
                detail
                or f'BulkSMS Nigeria refused the message. Check that the Sender '
                f'ID "{self.sender_id}" is registered and approved on the '
                f"account."
            )
        return detail or f"BulkSMS Nigeria returned HTTP {code}."


def _flatten(value) -> str:
    """Their errors arrive as a string, a list, or a field -> messages dict."""
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = [_flatten(item) for item in value.values()]
        return " ".join(part for part in parts if part)
    if isinstance(value, (list, tuple)):
        parts = [_flatten(item) for item in value]
        return " ".join(part for part in parts if part)
    return str(value)


def _recipient_of(data) -> str:
    if isinstance(data, dict):
        return str(data.get("to") or data.get("recipient") or "")
    return ""


def _mask(number: str) -> str:
    """Log the tail only. A server log is not the place for a parent's number."""
    return f"...{number[-4:]}" if len(number) > 4 else "..."
