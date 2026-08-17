"""Field-level validation for student records.

Phone numbers are the one field here worth validating properly: a parent's
number is how the school reaches them about money, and a typo in it is only
discovered at the moment it matters. Everything else on a student record is a
name or a date, where the school knows better than we do.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError

#: Digits only, once punctuation has been stripped.
_DIGITS = re.compile(r"\D+")

#: Nigerian mobile networks all issue 070/080/081/090/091 prefixes.
_LOCAL_MOBILE = re.compile(r"^0[789][01]\d{8}$")


def normalise_phone(value: str) -> str:
    """Reduce a typed number to the canonical local form, ``08031234567``.

    Accepts the three ways a Nigerian number gets written -- ``0803 123 4567``,
    ``+234 803 123 4567`` and ``234...`` -- because a parent's number arrives
    from whichever of them the front desk had in front of them.
    """
    if not value:
        return ""
    digits = _DIGITS.sub("", value)
    if digits.startswith("234") and len(digits) > 10:
        digits = "0" + digits[3:]
    return digits


def validate_phone(value: str) -> None:
    """Reject anything that is not a Nigerian mobile number.

    Deliberately mobile-only: this number exists to be called and texted about
    fees, and a landline cannot receive the payment reminders the messaging
    layer will send.
    """
    if not value:
        return
    digits = normalise_phone(value)
    if not _LOCAL_MOBILE.match(digits):
        raise ValidationError(
            "Enter a Nigerian mobile number, e.g. 0803 123 4567 or "
            "+234 803 123 4567.",
            code="invalid_phone",
        )


def format_phone(value: str) -> str:
    """Group a stored number for display: ``0803 123 4567``."""
    digits = normalise_phone(value)
    if len(digits) == 11:
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    return value


def normalise_admission_number(value: str) -> str:
    """Trim and collapse internal whitespace, keeping the school's own casing.

    ``FA/2025/001`` and ``fa/2025/001`` are the same admission number, but which
    one gets printed on the report card is the school's decision, not ours --
    so the clash is caught by a case-insensitive constraint rather than by
    rewriting what was typed.
    """
    return " ".join((value or "").split())
