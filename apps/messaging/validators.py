"""Validation for a school's messaging identity.

The Sender ID is the name a parent sees a message come from, and it is the one
field on the platform that is validated against somebody else's rules rather
than ours: every Nigerian gateway takes it from the same GSM alphanumeric
originating-address spec, and one they will not accept is worth catching on the
form rather than discovering when the first fee reminder bounces.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError

#: The GSM spec allows eleven characters in an alphanumeric originating
#: address. Every gateway we talk to enforces it, so we do too.
SENDER_ID_MAX_LENGTH = 11

#: Letters, digits, spaces and hyphens. Deliberately narrower than what the
#: spec technically permits: punctuation is the thing gateways silently mangle
#: or reject at registration, and a school gains nothing from an ampersand.
_ALLOWED = re.compile(r"^[A-Za-z0-9 \-]+$")

_HAS_LETTER = re.compile(r"[A-Za-z]")


def normalise_sender_id(value: str) -> str:
    """Trim and collapse internal whitespace, keeping the school's own casing.

    ``Dapgroup`` and ``DAPGROUP`` are the same registration to a gateway, but
    which one appears on a parent's handset is the school's decision, not ours.
    """
    return " ".join((value or "").split())


def validate_sender_id(value: str) -> None:
    """Reject anything a gateway would refuse to register.

    Every message below names the fix, because the person reading it is a
    proprietor choosing what their school is called on a phone, not an engineer.
    """
    sender_id = normalise_sender_id(value)
    if not sender_id:
        return

    if len(sender_id) > SENDER_ID_MAX_LENGTH:
        raise ValidationError(
            f"A Sender ID can be at most {SENDER_ID_MAX_LENGTH} characters. "
            f'"{sender_id}" is {len(sender_id)}.',
            code="sender_id_too_long",
        )
    if not _ALLOWED.match(sender_id):
        raise ValidationError(
            "A Sender ID can only contain letters, numbers, spaces and hyphens.",
            code="sender_id_charset",
        )
    if not _HAS_LETTER.search(sender_id):
        # An all-digit originating address is a short code, which is a
        # different product with a different registration and different
        # delivery behaviour. Letting one be typed here would produce a config
        # that looks approved and does not work.
        raise ValidationError(
            "A Sender ID needs at least one letter. An all-numeric sender is a "
            "short code, which is registered separately.",
            code="sender_id_numeric",
        )
