"""The two account-creation commands: platform owners, and invited school owners.

What must hold: a password is never something the operator passes or learns, a
platform owner belongs to no school and lands on the platform overview, and an
invited owner joins the school that exists rather than conjuring a second one.
"""

from __future__ import annotations

import re
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.roles import Role
from apps.schools.models import Branch, School

User = get_user_model()

STRONG = "violet-harbour-lantern-92"
PROMPT = "apps.accounts.management.commands.create_platform_owner.getpass"
RESET_PATH = re.compile(r"/accounts/reset/[^/\s]+/[^/\s]+/")


class Terminal:
    """Stands in for an interactive stdin."""

    def __init__(self, tty=True):
        self.tty = tty

    def isatty(self):
        return self.tty


def create_platform_owner(**options):
    options.setdefault("stdin", Terminal())
    return call_command("create_platform_owner", stdout=StringIO(), **options)


@override_settings(PUBLIC_BASE_URL="https://www.schoolcord.test")
class CreatePlatformOwnerTests(TestCase):
    def test_creates_a_superuser_platform_owner_with_no_school(self):
        with mock.patch(PROMPT, side_effect=[STRONG, STRONG]):
            create_platform_owner(email="Ada@SchoolCord.test", name="Ada Obi")

        user = User.objects.get(email__iexact="ada@schoolcord.test")
        self.assertEqual(user.role, Role.PLATFORM_OWNER)
        self.assertIsNone(user.school)
        self.assertIsNone(user.branch)
        self.assertTrue(user.is_superuser and user.is_staff and user.is_active)
        self.assertEqual((user.first_name, user.last_name), ("Ada", "Obi"))
        self.assertTrue(user.check_password(STRONG))

    def test_signing_in_lands_on_the_platform_overview_not_a_school(self):
        with mock.patch(PROMPT, side_effect=[STRONG, STRONG]):
            create_platform_owner(email="ada@schoolcord.test", name="Ada Obi")
        user = User.objects.get(email="ada@schoolcord.test")

        response = self.client.post(
            reverse("login"), {"username": user.username, "password": STRONG}, follow=True
        )
        self.assertEqual(response.redirect_chain[-1][0], reverse("core:platform_overview"))
        self.assertTemplateUsed(response, "core/dashboard_platform.html")
        self.assertRedirects(
            self.client.get(reverse("core:school_dashboard")), reverse("core:platform_overview")
        )

    def test_the_password_cannot_be_passed_as_an_argument(self):
        with self.assertRaises(TypeError):
            create_platform_owner(email="ada@schoolcord.test", name="Ada Obi", password=STRONG)
        self.assertFalse(User.objects.exists())

    def test_a_duplicate_email_is_refused_whatever_its_case(self):
        User.objects.create_user("existing", email="ada@schoolcord.test", role=Role.PLATFORM_OWNER)
        with mock.patch(PROMPT, side_effect=[STRONG, STRONG]):
            with self.assertRaisesMessage(CommandError, "already exists"):
                create_platform_owner(email="ADA@schoolcord.test", name="Ada Obi")
        self.assertEqual(User.objects.count(), 1)

    def test_mismatched_passwords_create_nothing(self):
        with mock.patch(PROMPT, side_effect=[STRONG, STRONG + "x"]):
            with self.assertRaisesMessage(CommandError, "do not match"):
                create_platform_owner(email="ada@schoolcord.test", name="Ada Obi")
        self.assertFalse(User.objects.exists())

    def test_a_weak_password_is_refused_by_the_signup_validators(self):
        with mock.patch(PROMPT, side_effect=["password", "password"]):
            with self.assertRaisesMessage(CommandError, "Password refused"):
                create_platform_owner(email="ada@schoolcord.test", name="Ada Obi")
        self.assertFalse(User.objects.exists())

    def test_without_a_terminal_it_will_not_prompt(self):
        with self.assertRaisesMessage(CommandError, "interactive terminal"):
            create_platform_owner(email="ada@schoolcord.test", name="Ada Obi", stdin=Terminal(tty=False))
        self.assertFalse(User.objects.exists())

    def test_email_link_sends_the_reset_email_and_nobody_knows_the_password(self):
        with mock.patch(PROMPT) as prompt:
            create_platform_owner(email="client@schoolcord.test", name="Client Name", email_link=True)
        prompt.assert_not_called()

        user = User.objects.get(email="client@schoolcord.test")
        self.assertTrue(user.has_usable_password())  # or the reset form would skip them
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["client@schoolcord.test"])
        self.assertIn("https://www.schoolcord.test/accounts/reset/", mail.outbox[0].body)


@override_settings(PUBLIC_BASE_URL="https://www.schoolcord.test")
class InviteSchoolOwnerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = School.all_objects.create(name="Fulfilled Academy")
        cls.branch = Branch.all_objects.create(school=cls.school, name="Main Campus")

    def invite(self, **options):
        options = {"school": "fulfilled-academy", "email": "proprietor@fulfilled.test",
                   "name": "Grace Adeyemi", **options}
        return call_command("invite_school_owner", stdout=StringIO(), **options)

    def test_joins_the_existing_school_and_creates_no_other(self):
        self.invite()
        self.assertEqual(School.all_objects.count(), 1)
        self.assertEqual(Branch.all_objects.count(), 1)
        owner = User.objects.get(email="proprietor@fulfilled.test")
        self.assertEqual(owner.school, self.school)
        self.assertIsNone(owner.branch)
        self.assertEqual(owner.role, Role.SCHOOL_OWNER)
        self.assertFalse(owner.is_staff or owner.is_superuser)

    def test_an_unknown_school_is_an_error_not_a_new_school(self):
        with self.assertRaisesMessage(CommandError, "never creates a school"):
            self.invite(school="fulfilled-academyy")
        self.assertEqual(School.all_objects.count(), 1)
        self.assertFalse(User.objects.exists())

    def test_a_duplicate_email_is_refused(self):
        User.objects.create_user("taken", email="Proprietor@Fulfilled.test", school=self.school)
        with self.assertRaisesMessage(CommandError, "already exists"):
            self.invite()

    def test_the_owner_sets_their_own_password_from_the_email(self):
        self.invite()
        owner = User.objects.get(email="proprietor@fulfilled.test")

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["proprietor@fulfilled.test"])
        match = RESET_PATH.search(message.body)
        self.assertIsNotNone(match, message.body)

        form_page = self.client.get(match.group(), follow=True)
        self.assertContains(form_page, "Set a new password")
        self.client.post(
            form_page.redirect_chain[-1][0],
            {"new_password1": STRONG, "new_password2": STRONG},
        )
        owner.refresh_from_db()
        self.assertTrue(owner.check_password(STRONG))

        signed_in = self.client.post(reverse("login"), {"username": owner.username, "password": STRONG})
        self.assertRedirects(signed_in, reverse("core:school_dashboard"), fetch_redirect_response=False)

    def test_no_email_sends_nothing_but_forgot_password_still_reaches_them(self):
        self.invite(no_email=True)
        self.assertEqual(len(mail.outbox), 0)
        self.client.post(reverse("password_reset"), {"email": "proprietor@fulfilled.test"})
        self.assertEqual(len(mail.outbox), 1)
