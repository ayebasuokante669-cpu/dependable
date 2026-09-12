"""The breached-password check.

Wraps ``pwned-passwords-django``'s validator rather than reimplementing it.
The k-anonymity protocol is the part worth not hand-rolling: the password is
SHA-1'd locally, only the **first five hex characters** of that hash are sent to
``api.pwnedpasswords.com``, and the several hundred full hashes that come back
are matched against the rest of the digest here. The API never sees the
password, and never sees enough of the hash to identify it.

Two behaviours are worth being explicit about, because both are the opposite of
what a "security check" usually does:

**It fails open.** If the API times out or errors, the library logs and falls
back to Django's local :class:`CommonPasswordValidator`. That validator is
already listed separately in ``AUTH_PASSWORD_VALIDATORS``, so the fallback
rejects nothing the rest of the policy would not have rejected anyway -- the
net effect of an outage is that the breach check is skipped, not that anybody
is locked out of setting a password. ``test_api_failure_lets_a_good_password_through``
pins this down.

**It can be switched off.** ``PWNED_PASSWORDS_ENABLED`` exists so the test suite
does not make a network call every time a form validates a password. It
defaults to on everywhere except under ``manage.py test``.
"""

from __future__ import annotations

import logging
import typing

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_variables
from pwned_passwords_django.validators import PwnedPasswordsValidator

logger = logging.getLogger(__name__)


class BreachedPasswordValidator(PwnedPasswordsValidator):
    """``PwnedPasswordsValidator`` with our copy and an off switch."""

    #: What the user sees. The upstream default is "This password is too
    #: common", which is both inaccurate -- the password may be unique to them,
    #: it is the *breach* that is the problem -- and unactionable. This says
    #: what happened and what to do, without implying we know their account is
    #: affected.
    default_error_message = _(
        "This password has appeared in a known data breach, so it is not safe "
        "to use here. Pick a different one — the Generate button will make you "
        "a strong one."
    )

    #: Shown under the field before anything is typed.
    default_help_message = _(
        "Any length from 8 characters. Checked against known breached "
        "passwords; nothing you type is sent anywhere."
    )

    def __init__(self, error_message=None, help_message=None, **kwargs):
        super().__init__(
            error_message=error_message or self.default_error_message,
            help_message=help_message or self.default_help_message,
            **kwargs,
        )

    @sensitive_variables()
    def validate(
        self, password: str, user: typing.Optional[AbstractBaseUser] = None
    ) -> None:
        if not getattr(settings, "PWNED_PASSWORDS_ENABLED", True):
            # Off under the test runner. Deliberately silent: logging a warning
            # on every password in a 650-test suite would bury real ones.
            return
        super().validate(password, user)
