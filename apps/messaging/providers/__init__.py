"""Provider selection.

Which gateway carries a message is now two decisions, not one:

* **the school's own** -- ``SchoolMessagingConfig.provider``, alongside the
  Sender ID it registered with that gateway. This is what production uses, and
  it is why two schools on the platform can be on two different gateways.
* **the platform override** -- ``settings.MESSAGING_PROVIDER``. When set it
  wins over every school's choice. It defaults to ``console``, so a fresh
  checkout sends nothing anywhere; production leaves it empty and each school
  goes out through its own gateway.

An override is not a fallback: it is the switch you reach for in development,
in tests, and on the day a gateway is down and nothing should be attempted.
Because it is explicit, "why did this go out through BulkSMS Nigeria?" always
has an answer in one of two places, and never in a silent default.

:func:`get_provider` is the only way the rest of the app obtains a provider.
Nothing outside this package imports a vendor class by name.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .africastalking import AfricasTalkingProvider
from .base import (
    Channel,
    DeliveryStatus,
    MessagePurpose,
    MessagingProvider,
    ProviderKey,
    ProviderNotConfigured,
    SenderIdentity,
    SendResult,
    TemplateMessage,
)
from .bulksmsnigeria import BulkSMSNigeriaProvider
from .console import ConsoleProvider
from .termii import TermiiProvider
from .termii_whatsapp import TermiiWhatsAppProvider

#: Every provider the platform knows how to talk to, keyed by ProviderKey.
PROVIDERS: dict[str, type[MessagingProvider]] = {
    ConsoleProvider.key: ConsoleProvider,
    BulkSMSNigeriaProvider.key: BulkSMSNigeriaProvider,
    TermiiProvider.key: TermiiProvider,
    TermiiWhatsAppProvider.key: TermiiWhatsAppProvider,
    AfricasTalkingProvider.key: AfricasTalkingProvider,
}


def providers_for(channel: str) -> list[str]:
    """The provider keys that can carry ``channel``, in registry order."""
    return [key for key, cls in PROVIDERS.items() if channel in cls.channels]

DEFAULT_PROVIDER = ProviderKey.CONSOLE


def platform_override() -> str:
    """``settings.MESSAGING_PROVIDER``, or empty when schools choose for themselves.

    Absent from settings entirely means ``console``: a deployment that has
    never thought about messaging must not discover it by texting parents.
    """
    return (getattr(settings, "MESSAGING_PROVIDER", DEFAULT_PROVIDER) or "").strip()


def get_provider(
    key: str | None = None, *, identity: SenderIdentity | None = None
) -> MessagingProvider:
    """Build the provider that should carry ``identity``'s messages.

    Precedence, highest first: an explicit ``key`` (callers that mean one
    specific gateway, and tests), the platform override, then the gateway on
    the school's own config.

    An unknown key is a configuration error rather than a silent fallback: a
    school that thinks it is sending real SMS must not quietly be writing to a
    log file because someone mistyped the setting.
    """
    chosen = key or platform_override()
    if not chosen and identity is not None:
        chosen = identity.provider_key
    chosen = chosen or DEFAULT_PROVIDER

    try:
        provider_class = PROVIDERS[chosen]
    except KeyError:
        source = (
            "MESSAGING_PROVIDER" if chosen == platform_override()
            else "That school's messaging provider"
        )
        raise ImproperlyConfigured(
            f'{source} = "{chosen}" is not a provider. '
            f"Choose one of: {', '.join(sorted(PROVIDERS))}."
        ) from None
    return provider_class(identity)


__all__ = [
    "AfricasTalkingProvider",
    "BulkSMSNigeriaProvider",
    "Channel",
    "ConsoleProvider",
    "DEFAULT_PROVIDER",
    "DeliveryStatus",
    "MessagePurpose",
    "MessagingProvider",
    "PROVIDERS",
    "ProviderKey",
    "ProviderNotConfigured",
    "SendResult",
    "SenderIdentity",
    "TemplateMessage",
    "TermiiProvider",
    "TermiiWhatsAppProvider",
    "get_provider",
    "platform_override",
    "providers_for",
]
