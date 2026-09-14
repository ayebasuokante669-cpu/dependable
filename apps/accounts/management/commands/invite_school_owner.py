"""Give an existing school a real owner, who sets their own password by email.

    python manage.py invite_school_owner --school fulfilled-academy \\
        --email proprietor@example.com --name "Their Name"

For a school that already exists (a pilot set up by the platform, say) and
needs its owner's account, not for a new school: signup does that. The school is
looked up by slug and never created, so a typo is an error, not a duplicate
tenant.

The account gets a random password that is never shown, and the owner is
emailed the password-reset link to choose their own. Nobody who ran this command
knows it. If the link expires (PASSWORD_RESET_TIMEOUT), they use "Forgot
password" on the sign-in page, which sends the same email.

Run it where outgoing mail is configured, i.e. production. The dev settings print
mail to the console. Pass --no-email to create the account now and send the
email later from the sign-in page's "Forgot password".
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import BaseUserManager
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction

from apps.accounts.invites import send_set_password_email, set_random_password, unique_username
from apps.core.roles import Role
from apps.schools.models import School


class Command(BaseCommand):
    help = "Create an owner account on an existing school and email them a set-password link."

    def add_arguments(self, parser):
        parser.add_argument("--school", required=True, help="The school's slug, e.g. fulfilled-academy.")
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True, help='Full name, e.g. "Ada Obi".')
        parser.add_argument("--job-title", default="Proprietor")
        parser.add_argument("--no-email", action="store_true", help="Create the account but send nothing.")
        parser.add_argument("--base-url", help="Site address for the emailed link. Defaults to PUBLIC_BASE_URL.")

    def handle(self, *args, **options):
        User = get_user_model()

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

        first, _, last = " ".join(options["name"].split()).partition(" ")
        owner = User(
            username=unique_username(email),
            email=email,
            first_name=first[:150],
            last_name=last[:150],
            role=Role.SCHOOL_OWNER,
            school=school,
            # No branch, as at signup: an owner sees every campus.
            branch=None,
            job_title=options["job_title"],
        )
        set_random_password(owner)
        try:
            owner.full_clean()
        except ValidationError as error:
            raise CommandError(f"Account not created: {error}")

        with transaction.atomic():
            owner.save()
            if not options["no_email"]:
                send_set_password_email(owner, base_url=options["base_url"])

        self.stdout.write(self.style.SUCCESS(
            f"Created owner {owner.username} <{email}> for {school.name}."
        ))
        others = User.objects.filter(school=school, role=Role.SCHOOL_OWNER, is_active=True).exclude(pk=owner.pk)
        if others.exists():
            self.stdout.write(self.style.WARNING(
                "Other active owners on this school: " + ", ".join(u.username for u in others)
            ))
        if options["no_email"]:
            self.stdout.write("No email sent. They can use \"Forgot password\" on the sign-in page.")
        else:
            self.stdout.write(f"A link to set their password was emailed to {email}.")
