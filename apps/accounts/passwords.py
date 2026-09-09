"""Generating a strong password.

A school setting up a bursar's account should not have to invent a password on
the spot, and the ones invented on the spot are the ones that end up in a
breach corpus. This produces one that passes the same policy the form enforces.

The shape is ``aBcD-2Efg-HjKm-3npQ``: four groups of four, from an alphabet with
the lookalike characters removed. That is ~93 bits of entropy, and it survives
being read down a phone line or copied off a printout -- which is how a
generated password actually reaches the person it belongs to.

Compliance is guaranteed by construction, not by argument: every candidate is
put through :func:`django.contrib.auth.password_validation.validate_password`,
the same call the signup and password-reset forms make, and rejected candidates
are discarded. If the policy is tightened later, this keeps agreeing with it
with no changes here.
"""

from __future__ import annotations

import secrets

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

#: Upper- and lowercase letters and digits, minus every pair that is ambiguous
#: in a sans-serif face or when spoken aloud: I/l/1, O/0. What is left is 56
#: characters, so each one carries ~5.8 bits.
UNAMBIGUOUS_ALPHABET = (
    "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I, no O
    "abcdefghijkmnpqrstuvwxyz"  # no l, no o
    "23456789"  # no 0, no 1
)

GROUPS = 4
GROUP_SIZE = 4
SEPARATOR = "-"

#: A candidate is only rejected by the policy for reasons that are themselves
#: random -- an all-digit draw, say, at a probability around 1e-13. A dozen
#: attempts is far past the point where failure means the policy has changed
#: into something this cannot satisfy, which is worth an exception rather than
#: an infinite loop.
MAX_ATTEMPTS = 12


def _candidate() -> str:
    """One draw. ``secrets``, not ``random``: this is key material."""
    return SEPARATOR.join(
        "".join(secrets.choice(UNAMBIGUOUS_ALPHABET) for _ in range(GROUP_SIZE))
        for _ in range(GROUPS)
    )


def generate_password() -> str:
    """Return a password that satisfies ``AUTH_PASSWORD_VALIDATORS``.

    :raises RuntimeError: if the configured policy rejected every attempt,
        which means the policy cannot be satisfied by this generator's shape
        and needs a human to look at it.
    """
    for _ in range(MAX_ATTEMPTS):
        candidate = _candidate()
        try:
            # No user is passed: UserAttributeSimilarityValidator has nothing to
            # compare against at generation time, and a random string is not
            # going to resemble an email address anyway.
            validate_password(candidate)
        except ValidationError:
            continue
        return candidate

    raise RuntimeError(
        f"Could not generate a password satisfying AUTH_PASSWORD_VALIDATORS in "
        f"{MAX_ATTEMPTS} attempts. The policy has probably grown a rule "
        f"({GROUPS}x{GROUP_SIZE} characters from a {len(UNAMBIGUOUS_ALPHABET)}-"
        f"character alphabet) cannot satisfy."
    )
