"""The two account-creation commands: platform owners, and invited school owners.

What must hold: a password is never something passed as an argument, a platform
owner belongs to no school and lands on the platform overview, an invited owner
joins the school that exists rather than conjuring a second one, and a temporary
password has to be replaced before the account can do anything else.
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
CHOSEN = "copper-meadow-violin-47"
PROMPT = "apps.accounts.invites.getpass"
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
        self.assertFalse(user.must_change_password)
        self.assertEqual((user.first_name, user.last_name), ("Ada", "Obi"))
        self.assertTrue(user.check_password(STRONG))

    def test_signing_in_lands_on_the_platform_overview_not_a_school(self):
        with mock.patch(PROMPT, side_effect=[STRONG, STRONG]):
            create_platform_owner(email="ada@schoolcord.test", name="Ada Obi")

        response = self.client.post(
            reverse("login"), {"username": "ada@schoolcord.test", "password": STRONG}, follow=True
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

    def test_a_temporary_password_must_be_changed_at_first_sign_in(self):
        with mock.patch(PROMPT, side_effect=[STRONG, STRONG]):
            create_platform_owner(
                email="client@placeholder.test", name="Client Name", temporary_password=True
            )
        user = User.objects.get(email="client@placeholder.test")
        self.assertTrue(user.must_change_password)
        self.assertEqual(len(mail.outbox), 0)

        response = self.client.post(
            reverse("login"), {"username": "client@placeholder.test", "password": STRONG}, follow=True
        )
        self.assertEqual(response.redirect_chain[-1][0], reverse("accounts:settings"))

    def test_email_link_and_temporary_password_cannot_be_combined(self):
        with self.assertRaisesMessage(CommandError, "not both"):
            create_platform_owner(
                email="ada@schoolcord.test", name="Ada Obi", email_link=True, temporary_password=True
            )
        self.assertFalse(User.objects.exists())


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
        self.assertFalse(owner.must_change_password)

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

        signed_in = self.client.post(reverse("login"), {"username": owner.email, "password": STRONG})
        self.assertRedirects(signed_in, reverse("core:school_dashboard"), fetch_redirect_response=False)

    def test_no_email_sends_nothing_but_forgot_password_still_reaches_them(self):
        self.invite(no_email=True)
        self.assertEqual(len(mail.outbox), 0)
        self.client.post(reverse("password_reset"), {"email": "proprietor@fulfilled.test"})
        self.assertEqual(len(mail.outbox), 1)

    def test_a_placeholder_owner_gets_a_typed_temporary_password_and_fixes_both(self):
        with mock.patch(PROMPT, side_effect=[STRONG, STRONG]) as prompt:
            self.invite(
                email="owner@placeholder.test", username="fulfilled-owner",
                temporary_password=True, stdin=Terminal(),
            )
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(len(mail.outbox), 0)

        owner = User.objects.get(username="fulfilled-owner")
        self.assertEqual(owner.school, self.school)
        self.assertTrue(owner.must_change_password)
        self.assertTrue(owner.check_password(STRONG))

        # Signing in goes straight to Account settings...
        response = self.client.post(
            reverse("login"), {"username": "fulfilled-owner", "password": STRONG}, follow=True
        )
        self.assertEqual(response.redirect_chain[-1][0], reverse("accounts:settings"))
        self.assertContains(response, "temporary password")

        # ...where choosing their own password lets them into the school...
        self.client.post(reverse("accounts:settings"), {
            "form": "password", "old_password": STRONG,
            "new_password1": CHOSEN, "new_password2": CHOSEN,
        })
        owner.refresh_from_db()
        self.assertFalse(owner.must_change_password)
        self.assertEqual(self.client.get(reverse("core:school_dashboard")).status_code, 200)

        # ...and correcting the placeholder email moves their sign-in with it.
        self.client.post(reverse("accounts:settings"), {
            "form": "email", "email": "grace@fulfilled.test", "current_password": CHOSEN,
        })
        owner.refresh_from_db()
        self.assertEqual(owner.email, "grace@fulfilled.test")
        self.client.logout()
        self.assertTrue(self.client.login(username="grace@fulfilled.test", password=CHOSEN))

    def test_a_temporary_password_is_never_emailed_even_without_no_email(self):
        with mock.patch(PROMPT, side_effect=[STRONG, STRONG]):
            self.invite(temporary_password=True, stdin=Terminal())
        self.assertEqual(len(mail.outbox), 0)
