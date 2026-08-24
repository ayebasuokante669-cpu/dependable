"""Provider selection.

``settings.MESSAGING_PROVIDER`` names one key from :data:`PROVIDERS`, and
:func:`get_provider` is the only way the rest of the app obtains one. Nothing
outside this package imports a vendor class by name.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .africastalking import AfricasTalkingProvider
from .base import (
    Channel,
    DeliveryStatus,
    MessagingProvider,
    ProviderNotConfigured,
    SendResult,
)
from .console import ConsoleProvider
from .termii import TermiiProvider

#: Every provider the platform knows how to talk to, by settings key.
PROVIDERS: dict[str, type[MessagingProvider]] = {
    ConsoleProvider.key: ConsoleProvider,
    TermiiProvider.key: TermiiProvider,
    AfricasTalkingProvider.key: AfricasTalkingProvider,
}

DEFAULT_PROVIDER = ConsoleProvider.key


def get_provider(key: str | None = None) -> MessagingProvider:
    """The active provider, or the one named by ``key``.

    An unknown key is a configuration error rather than a silent fallback: a
    school that thinks it is sending real SMS must not quietly be writing to a
    log file because someone mistyped the setting.
    """
    key = key or getattr(settings, "MESSAGING_PROVIDER", DEFAULT_PROVIDER)
    try:
        provider_class = PROVIDERS[key]
    except KeyError:
        raise ImproperlyConfigured(
            f'MESSAGING_PROVIDER="{key}" is not a provider. '
            f"Choose one of: {', '.join(sorted(PROVIDERS))}."
        ) from None
    return provider_class()


__all__ = [
    "AfricasTalkingProvider",
    "Channel",
    "ConsoleProvider",
    "DEFAULT_PROVIDER",
    "DeliveryStatus",
    "MessagingProvider",
    "PROVIDERS",
    "ProviderNotConfigured",
    "SendResult",
    "TermiiProvider",
    "get_provider",
]
