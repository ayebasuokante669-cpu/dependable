"""Give an existing school a real owner.

    python manage.py invite_school_owner --school fulfilled-academy \\
        --email proprietor@example.com --name "Their Name"

    python manage.py invite_school_owner --school fulfilled-academy \\
        --email owner@placeholder.invalid --username fulfilled-owner \\
        --name "Their Name" --temporary-password

For a school that already exists (a pilot set up by the platform, say) and
needs its owner's account, not for a new school: signup does that. The school is
looked up by slug and never created, so a typo is an error, not a duplicate
tenant.

Two ways the owner gets a password, and neither passes it as an argument:

* **Their real email** (the default): a random password that is never shown,
  and the password-reset email so they choose their own. Nobody who ran this
  command knows it. If the link expires (PASSWORD_RESET_TIMEOUT), "Forgot
  password" on the sign-in page sends the same email. Run it where outgoing mail
  is configured, i.e. production -- the dev settings print mail to the console --
  or pass --no-email and send it later from "Forgot password".
* **A placeholder email** (``--temporary-password``): you type a temporary
  password at a hidden prompt and hand it over. Nothing is emailed. The owner
  must choose their own password the first time they sign in, and corrects the
  email on Account settings. ``--username`` gives them a neutral username rather
  than one derived from the placeholder.
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
from apps.schools.models import School


class Command(BaseCommand):
    help = "Create an owner account on an existing school."

    #: Tests hand in a stand-in for the terminal.
    stealth_options = ("stdin",)

    def add_arguments(self, parser):
        parser.add_argument("--school", required=True, help="The school's slug, e.g. fulfilled-academy.")
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True, help='Full name, e.g. "Ada Obi".')
        parser.add_argument("--username", help="Defaults to one derived from the email.")
        parser.add_argument("--job-title", default="Proprietor")
        parser.add_argument(
            "--temporary-password",
            action="store_true",
            help="Type a temporary password at a hidden prompt instead of emailing a link. "
            "It must be changed at first sign-in.",
        )
        parser.add_argument("--no-email", action="store_true", help="Create the account but send nothing.")
        parser.add_argument("--base-url", help="Site address for the emailed link. Defaults to PUBLIC_BASE_URL.")

    def handle(self, *args, **options):
        User = get_user_model()
        temporary = options["temporary_password"]

        school = School.all_objects.filter(slug=options["school"]).first()
        if school is None:
            slugs = ", ".join(School.all_objects.values_list("slug", flat=True)) or "none"
            raise CommandError(
                f"No school with slug {options['school']!r}. Existing: {slugs}. "
                f"Nothing was created. This command never creates a school."
            )

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
        owner = User(
            username=username,
            email=email,
            first_name=first[:150],
            last_name=last[:150],
            role=Role.SCHOOL_OWNER,
            school=school,
            # No branch, as at signup: an owner sees every campus.
            branch=None,
            job_title=options["job_title"],
        )
        if temporary:
            owner.set_password(prompt_for_password(owner, options.get("stdin") or sys.stdin))
            # After set_password, which clears the flag on every other route.
            owner.must_change_password = True
        else:
            set_random_password(owner)
        try:
            owner.full_clean()
        except ValidationError as error:
            raise CommandError(f"Account not created: {error}")

        send_email = not temporary and not options["no_email"]
        with transaction.atomic():
            owner.save()
            if send_email:
                send_set_password_email(owner, base_url=options["base_url"])

        self.stdout.write(self.style.SUCCESS(
            f"Created owner {owner.username} <{email}> for {school.name}."
        ))
        others = User.objects.filter(school=school, role=Role.SCHOOL_OWNER, is_active=True).exclude(pk=owner.pk)
        if others.exists():
            self.stdout.write(self.style.WARNING(
                "Other active owners on this school: " + ", ".join(u.username for u in others)
            ))
        if temporary:
            self.stdout.write(
                f"Temporary password set. They sign in with {owner.username}, must choose a new "
                f"password straight away, and can correct the email on Account settings."
            )
        elif send_email:
            self.stdout.write(f"A link to set their password was emailed to {email}.")
        else:
            self.stdout.write("No email sent. They can use \"Forgot password\" on the sign-in page.")
