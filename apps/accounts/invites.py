"""Accounts made by the platform on someone's behalf, and how they get a password.

Signup covers the school that creates itself. Two kinds of account cannot come
from there: the platform's own staff, and an owner the platform sets up for a
school that already exists. Both go through management commands, and this is
what those commands share.

A password is never a command-line argument. It is either typed at a hidden
prompt, or -- where the person is not at the keyboard -- the account is given a
long random password that is never printed or stored, and they are sent Django's
own password-reset email to choose theirs. Random rather than
``set_unusable_password()`` on purpose: Django's reset form skips accounts with
an unusable password, so an unusable one would make the only way in unreachable.
"""

from __future__ import annotations

import secrets
from getpass import getpass
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management.base import CommandError
from django.utils.text import slugify


def unique_username(email: str) -> str:
    """A free username derived from the email, the same way signup derives one."""
    User = get_user_model()
    base = (slugify(email.split("@")[0]) or "user")[:140]
    candidate, counter = base, 2
    while User.objects.filter(username__iexact=candidate).exists():
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def set_random_password(user) -> None:
    """Give ``user`` a password nobody knows, not even whoever ran the command."""
    user.set_password(secrets.token_urlsafe(48))


def prompt_for_password(user, stdin) -> str:
    """Read a password at a hidden prompt, twice, and apply the signup validators.

    ``user`` is the unsaved account, so the similarity validator can compare the
    password against its name and email.
    """
    if not stdin.isatty():
        raise CommandError(
            "The password is typed at a hidden prompt, which needs an interactive "
            "terminal. Run this in a terminal."
        )
    password = getpass("Password: ")
    if password != getpass("Password (again): "):
        raise CommandError("The two passwords do not match. Nothing was created.")
    try:
        validate_password(password, user)
    except ValidationError as error:
        raise CommandError("Password refused: " + " ".join(error.messages))
    return password


def public_site(base_url: str | None = None) -> tuple[str, bool]:
    """``(domain, use_https)`` for links in mail, which has no request to go on."""
    url = (base_url or settings.PUBLIC_BASE_URL or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ImproperlyConfigured(
            "Emailed links need the site's address. Set PUBLIC_BASE_URL (e.g. "
            "https://www.theschoolcord.com) or pass --base-url."
        )
    return parts.netloc, parts.scheme == "https"


def send_set_password_email(user, *, base_url: str | None = None) -> None:
    """Send ``user`` the password-reset email, exactly as the reset page would.

    Rendered with the reset view's own templates and branding, so the invitation
    and a "forgot password" email are the same message rather than two that drift.
    """
    # Imported here: core.views imports half the project, and accounts is
    # loaded before it.
    from apps.core.views import BrandedPasswordResetView as ResetView

    domain, use_https = public_site(base_url)
    form = PasswordResetForm(data={"email": user.email})
    if not form.is_valid():
        raise ValueError(f"Cannot email {user.email!r}: {form.errors.as_text()}")
    form.save(
        domain_override=domain,
        use_https=use_https,
        subject_template_name=ResetView.subject_template_name,
        email_template_name=ResetView.email_template_name,
        html_email_template_name=ResetView.html_email_template_name,
        extra_email_context=ResetView.extra_email_context,
    )
