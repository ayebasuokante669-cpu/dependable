"""Termii WhatsApp -- school-initiated WhatsApp, through approved templates.

**Built, and not yet live.** Nothing sends through this class until a school's
WhatsApp Business account has cleared Meta's review and a platform
administrator marks it approved on the school's messaging identity -- the same
gating a Sender ID goes through, applied to a different approval. Until then
:func:`apps.messaging.identity.resolve` refuses WhatsApp for that school, with
a sentence saying so, and this file is never reached.

**Why templates.** WhatsApp Business carries free text only inside a
conversation the other person opened. A fee reminder is the school starting the
conversation, and for that WhatsApp requires a *template*: text registered and
approved in advance, with only its variables changing per send. So this
provider does not take a body at all. It is handed a
:class:`~.base.TemplateMessage` -- the approved template's id plus one
recipient's values -- and sends that.

**The request**, per Termii's WhatsApp Template API
(https://developers.termii.com/templates)::

    POST {TERMII_BASE_URL}/api/send/template
    {
      "phone_number": "2348031234567",
      "device_id":    "<the school's connected WhatsApp number>",
      "template_id":  "<the approved template>",
      "api_key":      "<master or school key>",
      "data":         {"parent_name": "...", "message": "..."}
    }

and it answers in the same shape the SMS endpoint does (``code``,
``message_id``, ``balance``), so everything about reading the answer --
200-but-refused, no id means no success, ``SENT`` never ``DELIVERED``, the low
balance warning -- is inherited from :class:`~.termii.TermiiGateway` rather
than restated. Their media variant (``/api/send/template/media``) is not used:
nothing the compose screen sends has an attachment.

**The account model is the SMS one.** Same master key, same per-school
override. What is per school here is the ``device_id`` -- the WhatsApp
Business number the school connected to Termii -- in place of a Sender ID.

**One recipient per request**, for the reason the SMS provider gives: every
``MessageRecipient`` row carries its own reference.
"""

from __future__ import annotations

from apps.students.validators import to_international

from .base import (
    Channel,
    MessagePurpose,
    ProviderKey,
    ProviderNotConfigured,
    SendResult,
    TemplateMessage,
)
from .termii import TermiiGateway, _mask


class TermiiWhatsAppProvider(TermiiGateway):
    key = ProviderKey.TERMII_WHATSAPP
    label = "Termii WhatsApp"
    channels = (Channel.WHATSAPP,)
    # WhatsApp has no alphanumeric Sender ID. Parents see the school's WhatsApp
    # Business profile, which belongs to the connected device.
    requires_sender_id = False
    requires_device_id = True

    @property
    def device_id(self) -> str:
        return self.identity.device_id if self.identity else ""

    def check(self) -> None:
        # The school's device before the platform's key, for the reason the SMS
        # provider checks the Sender ID first: it names the right person.
        if not self.device_id:
            raise ProviderNotConfigured(
                "Termii WhatsApp needs the school's WhatsApp device ID, and none "
                "is set on its messaging identity. Copy it from the WhatsApp "
                "device on the Termii dashboard."
            )
        super().check()

    # -- the request --------------------------------------------------------

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/api/send/template"

    def payload(self, recipient: str, template: TemplateMessage) -> dict:
        """The request body. Built and testable without an account."""
        return {
            "phone_number": to_international(recipient, plus=False),
            "device_id": self.device_id,
            "template_id": template.template_id,
            "api_key": self.api_key,
            "data": {
                name: _one_line(value) for name, value in template.data.items()
            },
        }

    def send(
        self,
        recipient: str,
        message: str,
        channel: str,
        *,
        purpose: str = MessagePurpose.TRANSACTIONAL,
        template: TemplateMessage | None = None,
    ) -> SendResult:
        # ``message`` is not sent: whatever the sender typed reaches the parent
        # only as the template's <%message%> variable, which the dispatcher has
        # already put in ``template.data``. ``purpose`` is not sent either --
        # WhatsApp has no DND route, and the template's own category is what
        # WhatsApp prices and polices.
        self.check()
        if channel != Channel.WHATSAPP:
            return SendResult.failure(f"{self.label} only carries WhatsApp.")
        if template is None or not template.template_id:
            return SendResult.failure(
                "WhatsApp only carries messages sent as an approved template, "
                "and this one had none, so it was not sent."
            )
        missing = template.missing_variables()
        if missing:
            # Caught here rather than left to Termii, because WhatsApp would
            # refuse every recipient for the same reason and bill nothing for
            # the privilege of saying so thirty-two times.
            return SendResult.failure(
                f'Template "{template.name or template.template_id}" needs '
                f"{', '.join(missing)}, and no value was given, so it was not sent."
            )
        return self.post(self.payload(recipient, template))

    # -- their answers ------------------------------------------------------

    def log_context(self, payload: dict) -> tuple[str, str, str]:
        return (
            self.identity.school_name or self.sender_id if self.identity else "",
            _mask(str(payload.get("phone_number", ""))),
            f"template {payload.get('template_id', '?')}",
        )

    def refusal_hint(self) -> str:
        return (
            "Check that the template is approved by WhatsApp and the school's "
            "WhatsApp device is connected on the account."
        )


def _one_line(value) -> str:
    """A template variable as WhatsApp will accept it.

    Meta refuses a template parameter containing a newline, a tab, or more than
    four spaces in a row -- and refuses the whole message, not just the line. A
    compose box happily produces all three, so every value is collapsed to
    single-spaced text before it leaves.
    """
    return " ".join(str(value if value is not None else "").split())
