"""Template context for the password UI."""

from __future__ import annotations

from django.conf import settings
from django.urls import reverse


def _min_length(default: int = 8) -> int:
    """The configured MinimumLengthValidator length, or the NIST floor.

    Read from AUTH_PASSWORD_VALIDATORS rather than duplicated, so the number
    the checklist shows is the number the server actually enforces.
    """
    for validator in getattr(settings, "AUTH_PASSWORD_VALIDATORS", []):
        if validator.get("NAME", "").endswith("MinimumLengthValidator"):
            return int(validator.get("OPTIONS", {}).get("min_length", default))
    return default


def password_policy(request) -> dict:
    """Expose the bits of the policy the browser can usefully reflect.

    Deliberately not "the policy": the breach check and the common-password
    list cannot be evaluated client-side, and pretending otherwise would invite
    someone to treat this as the gate. It is the label on the gate.
    """
    return {
        "password_policy": {
            "minLength": _min_length(),
            "generateUrl": reverse("accounts:generate_password"),
        }
    }
