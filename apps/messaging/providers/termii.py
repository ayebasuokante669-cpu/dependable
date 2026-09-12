"""Termii -- the platform's gateway.

**Why this one.** BulkSMS Nigeria declined to support the arrangement this
platform is built on: one master account sending on behalf of many schools,
each under its own registered Sender ID. Termii is built for exactly that, so
it is now the default real gateway. :mod:`.bulksmsnigeria` stays in the tree
and still works -- being able to move between gateways without touching a view
is the entire point of this package -- it is simply no longer what a new
school's config defaults to.

**The account model** is unchanged, which is why nothing above this file moved.
SCHOOLCORD holds one Termii account and pays for the units; each school
registers its own alphanumeric Sender ID against it, and ``from`` on every
request is that school's, resolved per send from its ``SchoolMessagingConfig``.
There is deliberately no platform-wide sender: a school with no approved Sender
ID is refused before it reaches this class. A school that later takes out its
own Termii account puts its key on its config and the same code sends with it.

**Routing is the part that has to be right.** Termii's send endpoint offers
these channels, and picking the wrong one does not fail loudly -- it silently
fails to arrive:

* ``dnd`` -- the transactional route. Reaches subscribers on the Do-Not-Disturb
  register, which in Nigeria is most of them, and is exempt from the 8pm-8am
  restriction the generic route enforces.
* ``generic`` -- the promotional route. Blocked for DND numbers, refused
  overnight, and cheaper.
* ``whatsapp`` -- free text over WhatsApp. **Not used.** WhatsApp Business only
  carries free text inside a conversation the parent opened; a message the
  school starts has to be a template Meta approved in advance. Every message
  this platform sends is school-initiated, so WhatsApp goes through
  :mod:`.termii_whatsapp` and its template endpoint, and this provider carries
  SMS only.

A fee reminder sent on ``generic`` reaches perhaps a third of the parents it was
addressed to, at an hour of the school's choosing, and reports success for all
of them. So the channel is derived from the message's
:class:`~.base.MessagePurpose` rather than configured, and the default purpose
is transactional. See :meth:`TermiiProvider.termii_channel`.

**One recipient per request.** Termii has a bulk endpoint that takes an array
of numbers, and we deliberately do not use it: it returns a single
``message_id`` for the whole batch, and every ``MessageRecipient`` row here
carries its own provider reference and its own status. Collapsing thirty-two
rows onto one id would mean a bursar chasing one parent could not tell whether
that parent's copy was the one that failed.

Talks HTTP with ``urllib`` from the standard library rather than pulling in
``requests`` -- the same trade the BulkSMS Nigeria provider makes, and for the
same reason: one JSON POST does not justify a dependency the school's server
then has to keep patched.

Termii accepting a message means ``SENT``, never ``DELIVERED``. Their response
says the message entered the queue; whether a handset received it arrives later
on their delivery-report webhook. Reporting it as delivered would make the log
lie about the one thing a bursar chasing a parent needs it for.
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
    MessagePurpose,
    MessagingProvider,
    ProviderKey,
    ProviderNotConfigured,
    SendResult,
)

logger = logging.getLogger("schoolcord.messaging")

#: How long to wait on one send before giving up on it. A batch is sent in a
#: request cycle, so a gateway that has stopped answering must fail that
#: recipient quickly rather than hold the bursar's browser open.
TIMEOUT_SECONDS = 15

#: Termii's own name for the transactional route -- the one that reaches
#: Do-Not-Disturb numbers and is not curfewed overnight.
DND_CHANNEL = "dnd"
#: Their promotional route. Blocked for DND numbers, refused 8pm-8am.
GENERIC_CHANNEL = "generic"

#: What their API calls a plain text message, as opposed to a flash SMS.
MESSAGE_TYPE = "plain"


class TermiiGateway(MessagingProvider):
    """What every Termii product shares: the account, the POST, their answers.

    Not registered as a provider -- it cannot send anything by itself. SMS and
    WhatsApp subclass it because they are two endpoints on one account: the
    same key, the same response shape, the same ways of refusing.
    """

    label = "Termii"

    def __init__(self, identity=None):
        super().__init__(identity)
        self.base_url = getattr(
            settings, "TERMII_BASE_URL", "https://api.ng.termii.com"
        ).rstrip("/")

    # -- credentials --------------------------------------------------------

    @property
    def api_key(self) -> str:
        """The school's own key if it has one, otherwise the platform's.

        This is the whole "master account, per-school identity" arrangement in
        one property: for the pilot every school falls through to the platform
        key and is distinguished only by its Sender ID.
        """
        if self.identity is not None and self.identity.api_key:
            return self.identity.api_key
        return getattr(settings, "TERMII_API_KEY", "")

    def check(self) -> None:
        # Identity first: a missing Sender ID is the school's problem to fix
        # and a missing key is the platform's, and saying so in that order gets
        # the message to the right person.
        super().check()
        if not self.api_key:
            raise ProviderNotConfigured(
                "TERMII_API_KEY is not set, so no message can be sent. Set the "
                "platform's master key in the environment, or run on the "
                "console provider."
            )

    # -- the request --------------------------------------------------------

    @property
    def endpoint(self) -> str:
        raise NotImplementedError

    def post(self, payload: dict) -> SendResult:
        """POST ``payload`` to :attr:`endpoint`; every failure becomes a result."""
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8", errors="replace")
            return self.read_response(body, payload)
        except urllib.error.HTTPError as exc:
            # A 4xx carries Termii's own explanation -- an unregistered sender,
            # an exhausted balance, an invalid key -- which is far more use to
            # the bursar than "HTTP 400".
            detail = exc.read().decode("utf-8", errors="replace")
            return SendResult.failure(self.read_error(exc.code, detail))
        except urllib.error.URLError as exc:
            return SendResult.failure(f"Could not reach Termii: {exc.reason}.")
        except TimeoutError:
            return SendResult.failure(
                f"Termii did not answer within {TIMEOUT_SECONDS} seconds."
            )

    # -- their answers ------------------------------------------------------

    def read_response(self, body: str, payload: dict | None = None) -> SendResult:
        """Turn a 2xx body into a result.

        Termii answers 200 for messages it then refuses, so a 2xx is not on its
        own a success. Two shapes are accepted because their API returns both:
        the bulk endpoint answers ``{"code": "ok", "message_id": ...}`` and the
        single-send endpoint has historically answered with ``message_id`` and
        ``message: "Successfully Sent"`` and no ``code`` at all. A missing
        ``code`` is therefore not treated as a failure -- but a missing
        ``message_id`` is, because without one there is nothing to reconcile a
        later delivery report against.
        """
        try:
            parsed = json.loads(body or "{}")
        except ValueError:
            return SendResult.failure("Termii returned something that was not JSON.")

        if not isinstance(parsed, dict):
            return SendResult.failure("Termii returned an unexpected response.")

        code = str(parsed.get("code", "")).strip().lower()
        reference = str(parsed.get("message_id") or "").strip()
        detail = _flatten(parsed.get("message") or parsed.get("error") or "")

        # An explicit code that is not "ok" is a refusal, whatever else is in
        # the body.
        if code and code != "ok":
            return SendResult.failure(
                detail or f"Termii refused the message (code: {code})."
            )
        if not reference:
            return SendResult.failure(
                detail
                or "Termii accepted the request but returned no message id, so "
                "the message cannot be tracked."
            )

        balance = parsed.get("balance")
        sender, to, route = self.log_context(payload or {})
        logger.info(
            "[%s] %s -> %s on %s (ref %s, balance %s)",
            self.label,
            sender,
            to,
            route,
            reference,
            balance if balance is not None else "unknown",
        )
        if _is_low(balance):
            # Worth a louder line than the send itself: units running out stops
            # every school on the platform at once, and the first anyone would
            # otherwise know is a batch of failures.
            logger.warning(
                "[Termii] master account balance is down to %s units.", balance
            )

        # Accepted into their queue. Delivery is a later fact, reported by their
        # webhook -- see the module docstring.
        return SendResult(status=DeliveryStatus.SENT, reference=reference)

    def log_context(self, payload: dict) -> tuple[str, str, str]:
        """Who it went out as, to whom (masked), and on which route."""
        return (
            self.sender_id,
            _mask(str(payload.get("to", ""))),
            payload.get("channel", "?"),
        )

    def read_error(self, code: int, body: str) -> str:
        """One sentence a bursar or an administrator can act on."""
        try:
            parsed = json.loads(body or "{}")
        except ValueError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        detail = _flatten(parsed.get("message") or parsed.get("error") or "")

        if code in (401, 403):
            return (
                detail
                or f"Termii rejected the account credentials (HTTP {code}). "
                f"Check TERMII_API_KEY."
            )
        if code in (400, 422):
            return detail or f"Termii refused the message. {self.refusal_hint()}"
        if code == 429:
            return detail or "Termii is rate-limiting the account. Try again shortly."
        return detail or f"Termii returned HTTP {code}."

    def refusal_hint(self) -> str:
        """What to check when Termii refuses without saying why."""
        return ""


class TermiiProvider(TermiiGateway):
    """SMS through Termii, under the school's own Sender ID."""

    key = ProviderKey.TERMII
    label = "Termii"
    channels = (Channel.SMS,)

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/api/sms/send"

    def termii_channel(self, channel: str, purpose: str) -> str:
        """Which Termii route this message is allowed on.

        The answer is the purpose, and the mapping is one way round only:
        transactional goes on ``dnd``, and *only* something explicitly marked
        promotional goes on ``generic``.

        Written as "generic if promotional, else dnd" rather than as a lookup
        table on purpose. A dict would make an unrecognised purpose fall
        through to whatever ``.get()``'s default happened to be; this way every
        value that is not the one promotional case -- including a blank, a
        typo, or a purpose added later and not thought about here -- lands on
        the transactional route, which is the one that arrives.
        """
        if purpose == MessagePurpose.PROMOTIONAL:
            return GENERIC_CHANNEL
        return DND_CHANNEL

    def payload(
        self,
        recipient: str,
        message: str,
        channel: str,
        purpose: str = MessagePurpose.TRANSACTIONAL,
    ) -> dict:
        """The request body. Built and testable without an account.

        Termii wants the number in international form without the plus
        (``2348031234567``) and the API key in the body rather than in a header.
        """
        return {
            "to": to_international(recipient, plus=False),
            # The school's own registered Sender ID, never a platform-wide one.
            "from": self.sender_id,
            "sms": message,
            "type": MESSAGE_TYPE,
            "channel": self.termii_channel(channel, purpose),
            "api_key": self.api_key,
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
        return self.post(self.payload(recipient, message, channel, purpose))

    def refusal_hint(self) -> str:
        return (
            f'Check that the Sender ID "{self.sender_id}" is registered and '
            f"approved on the account."
        )


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


#: Below this many units left on the master account, every send logs a warning.
#: Not a refusal -- Termii is the one that knows what a message costs, and
#: guessing here would block sends the account could actually afford.
LOW_BALANCE_UNITS = 100


def _is_low(balance) -> bool:
    try:
        return float(balance) < LOW_BALANCE_UNITS
    except (TypeError, ValueError):
        return False


def _mask(number: str) -> str:
    """Log the tail only. A server log is not the place for a parent's number."""
    return f"...{number[-4:]}" if len(number) > 4 else "..."
