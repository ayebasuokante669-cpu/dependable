"""Create a SCHOOLCORD platform-owner account: staff of the platform, not of a school.

    python manage.py create_platform_owner --email you@theschoolcord.com --name "Your Name"
    python manage.py create_platform_owner --email them@example.com --name "Their Name" --email-link
    python manage.py create_platform_owner --email placeholder@example.com --name "Their Name" --temporary-password

The password is never a command-line argument, where it would end up in shell
history and process listings. One of:

* by default, it is typed at a hidden prompt, twice, and checked against the
  same validators signup uses (length, common passwords, Have I Been Pwned).
  For your own account.
* ``--email-link``: the account gets a random password nobody knows, and the
  person is emailed the password-reset link to choose their own.
* ``--temporary-password``: typed at the hidden prompt, but the account has to
  choose a new password the first time it signs in. For someone whose real
  email you do not have yet; they correct it on Account settings.

A platform owner has no school and is a Django superuser, which is what gives
them every school on the platform and the admin. Signing in lands on the
platform overview.
"""

from __future__ import annotations

import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import BaseUserManager
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction

from apps.accounts.invites import (
    prompt_for_password,
    send_set_password_email,
    set_random_password,
    unique_username,
)
from apps.core.roles import Role


class Command(BaseCommand):
    help = "Create a platform-owner account (no school). The password is never an argument."

    #: Tests hand in a stand-in for the terminal.
    stealth_options = ("stdin",)

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True, help='Full name, e.g. "Ada Obi".')
        parser.add_argument("--username", help="Defaults to one derived from the email.")
        parser.add_argument(
            "--email-link",
            action="store_true",
            help="Email a link to set the password instead of prompting for one.",
        )
        parser.add_argument(
            "--temporary-password",
            action="store_true",
            help="The prompted password is temporary: it must be changed at first sign-in.",
        )
        parser.add_argument(
            "--base-url",
            help="Site address for the emailed link. Defaults to PUBLIC_BASE_URL.",
        )

    def handle(self, *args, **options):
        if options["email_link"] and options["temporary_password"]:
            raise CommandError("Choose --email-link or --temporary-password, not both.")

        User = get_user_model()
        email = BaseUserManager.normalize_email(options["email"].strip())
        try:
            validate_email(email)
        except ValidationError:
            raise CommandError(f"{email!r} is not a valid email address.")
        if User.objects.filter(email__iexact=email).exists():
            raise CommandError(f"An account with {email} already exists.")

        username = options["username"] or unique_username(email)
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(f"The username {username!r} is taken.")

        first, _, last = " ".join(options["name"].split()).partition(" ")
        user = User(
            username=username,
            email=email,
            first_name=first[:150],
            last_name=last[:150],
            role=Role.PLATFORM_OWNER,
            school=None,
            branch=None,
            job_title="Platform owner",
            is_staff=True,
            is_superuser=True,
        )

        if options["email_link"]:
            set_random_password(user)
        else:
            user.set_password(prompt_for_password(user, options.get("stdin") or sys.stdin))
            # After set_password, which clears the flag on every other route.
            user.must_change_password = options["temporary_password"]

        try:
            user.full_clean()
        except ValidationError as error:
            raise CommandError(f"Account not created: {error}")

        with transaction.atomic():
            user.save()
            if options["email_link"]:
                # Inside the transaction: if the mail cannot be sent, no account
                # is left behind that nobody can get into.
                send_set_password_email(user, base_url=options["base_url"])

        self.stdout.write(self.style.SUCCESS(f"Created platform owner {username} <{email}>."))
        if options["email_link"]:
            self.stdout.write(f"A link to set the password was emailed to {email}.")
        if options["temporary_password"]:
            self.stdout.write("The password is temporary: they must choose a new one when they first sign in.")
        self.stdout.write(
            f"Sign in with {email} or {username}; it lands on the platform overview (/platform/)."
        )
