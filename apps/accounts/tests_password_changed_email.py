"""The "your password was changed" notice.

What must hold: a successful change -- from Account settings or from a reset
link -- emails the account's owner a branded notice that says when, and how to
take the account back if it was not them; the notice never carries the password;
a refused change sends nothing; an invitee choosing their *first* password is not
told it "was changed"; and a mail failure never turns a completed change into an
error page.
"""

from __future__ import annotations

import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.branding import PRODUCT_NAME
from apps.core.emails import send_password_changed_email
from apps.core.roles import Role
from apps.schools.models import Branch, School

User = get_user_model()

STRONG = "violet-harbour-lantern-92"
CHOSEN = "copper-meadow-violin-47"
SITE = "https://app.schoolcord.test"
RESET_PATH = re.compile(r"/accounts/reset/[^/\s]+/[^/\s]+/")


@override_settings(PUBLIC_BASE_URL=SITE)
class PasswordChangedEmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = School.all_objects.create(name="Riverbank Academy")
        cls.branch = Branch.all_objects.create(school=cls.school, name="Main Campus")
        cls.principal = User.objects.create_user(
            "principal", email="principal@riverbank.test", password=STRONG,
            first_name="Ngozi", last_name="Eze",
            role=Role.PRINCIPAL, school=cls.school, branch=cls.branch,
            last_login=timezone.now(),
        )

    def change_password(self, old, new):
        return self.client.post(reverse("accounts:settings"), {
            "form": "password", "old_password": old,
            "new_password1": new, "new_password2": new,
        })

    def reset_password_by_email(self, user, new):
        """The whole reset flow, as a signed-out person would walk it."""
        self.client.post(reverse("password_reset"), {"email": user.email})
        link = RESET_PATH.search(mail.outbox[-1].body).group()
        form_page = self.client.get(link, follow=True)
        return self.client.post(
            form_page.redirect_chain[-1][0],
            {"new_password1": new, "new_password2": new},
        )

    # --- From Account settings ---------------------------------------------

    def test_changing_it_on_account_settings_sends_one_notice(self):
        self.client.force_login(self.principal)
        response = self.change_password(STRONG, CHOSEN)

        self.assertRedirects(response, reverse("accounts:settings"),
                             fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.principal.email])
        self.assertEqual(message.subject, f"Your {PRODUCT_NAME} password was changed")

    def test_the_notice_says_how_to_take_the_account_back(self):
        self.client.force_login(self.principal)
        self.change_password(STRONG, CHOSEN)
        message = mail.outbox[0]
        reset_url = SITE + reverse("password_reset")

        self.assertIn(reset_url, message.body)
        self.assertIn("If it was not you", message.body)
        html = message.alternatives[0][0]
        self.assertIn(reset_url, html)
        self.assertIn("Your password was changed", html)
        # Branded: on the platform's own shell, with the product name on it.
        self.assertIn(PRODUCT_NAME, html)

    def test_the_notice_never_contains_either_password(self):
        self.client.force_login(self.principal)
        self.change_password(STRONG, CHOSEN)
        message = mail.outbox[0]
        for secret in (STRONG, CHOSEN):
            self.assertNotIn(secret, message.body)
            self.assertNotIn(secret, message.alternatives[0][0])

    def test_a_refused_change_sends_nothing(self):
        self.client.force_login(self.principal)
        response = self.change_password("not-my-password", CHOSEN)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_the_confirmation_on_screen_mentions_the_email(self):
        self.client.force_login(self.principal)
        response = self.change_password(STRONG, CHOSEN)
        page = self.client.get(response["Location"])
        self.assertContains(page, self.principal.email)

    def test_a_mail_failure_does_not_undo_or_break_the_change(self):
        self.client.force_login(self.principal)
        with mock.patch(
            "apps.core.emails.EmailMultiAlternatives.send",
            side_effect=ConnectionError("Resend is down"),
        ):
            response = self.change_password(STRONG, CHOSEN)

        self.assertRedirects(response, reverse("accounts:settings"),
                             fetch_redirect_response=False)
        self.principal.refresh_from_db()
        self.assertTrue(self.principal.check_password(CHOSEN))

    # --- From a reset link ---------------------------------------------------

    def test_resetting_it_by_email_sends_the_notice_too(self):
        self.reset_password_by_email(self.principal, CHOSEN)

        self.principal.refresh_from_db()
        self.assertTrue(self.principal.check_password(CHOSEN))
        # The reset link itself, then the notice.
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            mail.outbox[1].subject, f"Your {PRODUCT_NAME} password was changed"
        )
        self.assertEqual(mail.outbox[1].to, [self.principal.email])

    def test_an_invitee_choosing_their_first_password_is_not_told_it_changed(self):
        """The invitation is this same reset screen. An account that has never
        signed in is choosing a password, not changing one."""
        invitee = User.objects.create_user(
            "new.bursar", email="new.bursar@riverbank.test", password=STRONG,
            role=Role.BURSAR, school=self.school, branch=self.branch,
        )
        self.assertIsNone(invitee.last_login)

        self.reset_password_by_email(invitee, CHOSEN)

        invitee.refresh_from_db()
        self.assertTrue(invitee.check_password(CHOSEN))
        self.assertEqual(len(mail.outbox), 1)  # only the link itself

    # --- The sender on its own ----------------------------------------------

    @override_settings(PUBLIC_BASE_URL="")
    def test_without_a_public_address_nothing_is_sent(self):
        self.assertFalse(send_password_changed_email(self.principal))
        self.assertEqual(len(mail.outbox), 0)

    def test_an_account_with_no_email_is_skipped_quietly(self):
        self.principal.email = ""
        self.assertFalse(send_password_changed_email(self.principal))
        self.assertEqual(len(mail.outbox), 0)
