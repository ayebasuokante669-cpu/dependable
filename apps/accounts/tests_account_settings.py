"""Account settings, signing in by email, and being held on a temporary password.

What must hold: every role can change its own email and password from a page
the sidebar links to; a changed email never strands a login; the password
policy is the same one signup applies; and an account on a temporary password
can do nothing else until it has chosen its own.
"""

from __future__ import annotations

import re

from django.contrib.auth import authenticate, get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from apps.core.navigation import nav_for
from apps.core.roles import Role
from apps.schools.models import Branch, School

User = get_user_model()

STRONG = "violet-harbour-lantern-92"
CHOSEN = "copper-meadow-violin-47"
RESET_PATH = re.compile(r"/accounts/reset/[^/\s]+/[^/\s]+/")


class AccountFixtures(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = School.all_objects.create(name="Riverbank Academy")
        cls.branch = Branch.all_objects.create(school=cls.school, name="Main Campus")

        def make(username, **fields):
            return User.objects.create_user(
                username, email=f"{username}@riverbank.test", password=STRONG, **fields
            )

        cls.platform = make("platform", role=Role.PLATFORM_OWNER, is_staff=True, is_superuser=True)
        cls.owner = make("owner", role=Role.SCHOOL_OWNER, school=cls.school)
        cls.principal = make("principal", role=Role.PRINCIPAL, school=cls.school, branch=cls.branch)
        cls.bursar = make("bursar", role=Role.BURSAR, school=cls.school, branch=cls.branch)

    def change_password(self, old, new, confirm=None):
        return self.client.post(reverse("accounts:settings"), {
            "form": "password", "old_password": old,
            "new_password1": new, "new_password2": confirm or new,
        })

    def change_email(self, email, password):
        return self.client.post(reverse("accounts:settings"), {
            "form": "email", "email": email, "current_password": password,
        })


class SignInByEmailTests(AccountFixtures):
    def test_the_email_signs_in_whatever_its_case(self):
        response = self.client.post(
            reverse("login"), {"username": "OWNER@Riverbank.test", "password": STRONG}
        )
        self.assertRedirects(response, reverse("core:school_dashboard"), fetch_redirect_response=False)

    def test_the_username_still_signs_in(self):
        self.assertEqual(authenticate(username="bursar", password=STRONG), self.bursar)

    def test_a_wrong_password_with_the_email_is_refused(self):
        response = self.client.post(
            reverse("login"), {"username": "owner@riverbank.test", "password": "wrong"}
        )
        self.assertContains(response, "Please enter a correct")

    def test_an_email_two_accounts_share_signs_in_neither(self):
        User.objects.create_user("twin", email="Owner@riverbank.test", password=STRONG)
        self.assertIsNone(authenticate(username="owner@riverbank.test", password=STRONG))
        self.assertEqual(authenticate(username="owner", password=STRONG), self.owner)

    def test_an_inactive_account_cannot_sign_in_by_email(self):
        User.objects.filter(pk=self.owner.pk).update(is_active=False)
        self.assertIsNone(authenticate(username="owner@riverbank.test", password=STRONG))


class AccountSettingsPageTests(AccountFixtures):
    def test_it_needs_a_login(self):
        response = self.client.get(reverse("accounts:settings"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_every_role_can_open_it_and_finds_it_in_the_sidebar(self):
        for user in (self.platform, self.owner, self.principal, self.bursar):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.get(reverse("accounts:settings"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'href="{reverse("accounts:settings")}"')

                role = Role.PLATFORM_OWNER if user.is_superuser else user.role
                items = {i.label: i for s in nav_for(role, "/") for i in s.items}
                self.assertTrue(items["Account settings"].available)
                self.assertEqual(items["Account settings"].href, reverse("accounts:settings"))

    def test_the_old_change_password_address_leads_here(self):
        self.client.force_login(self.bursar)
        self.assertRedirects(
            self.client.get("/accounts/password_change/"), reverse("accounts:settings")
        )

    # -- email -------------------------------------------------------------

    def test_changing_the_email_needs_the_current_password(self):
        self.client.force_login(self.bursar)
        response = self.change_email("new@riverbank.test", "wrong")
        self.assertContains(response, "not your current password")
        self.bursar.refresh_from_db()
        self.assertEqual(self.bursar.email, "bursar@riverbank.test")

    def test_an_email_another_account_uses_is_refused(self):
        self.client.force_login(self.bursar)
        response = self.change_email("OWNER@riverbank.test", STRONG)
        self.assertContains(response, "Another account already uses")
        self.bursar.refresh_from_db()
        self.assertEqual(self.bursar.email, "bursar@riverbank.test")

    def test_a_changed_email_moves_the_login_and_keeps_the_username(self):
        self.client.force_login(self.bursar)
        self.assertRedirects(
            self.change_email("finance@riverbank.test", STRONG), reverse("accounts:settings")
        )
        self.bursar.refresh_from_db()
        self.assertEqual(self.bursar.email, "finance@riverbank.test")
        self.assertEqual(self.bursar.username, "bursar")

        self.client.logout()
        self.assertTrue(self.client.login(username="finance@riverbank.test", password=STRONG))
        self.client.logout()
        self.assertFalse(self.client.login(username="bursar@riverbank.test", password=STRONG))
        self.assertTrue(self.client.login(username="bursar", password=STRONG))

    # -- password ----------------------------------------------------------

    def test_a_wrong_old_password_is_refused(self):
        self.client.force_login(self.principal)
        response = self.change_password("wrong", CHOSEN)
        self.assertEqual(response.status_code, 200)
        self.principal.refresh_from_db()
        self.assertTrue(self.principal.check_password(STRONG))

    def test_a_weak_new_password_is_refused_by_the_signup_validators(self):
        self.client.force_login(self.principal)
        response = self.change_password(STRONG, "password")
        self.assertContains(response, "too common")
        self.principal.refresh_from_db()
        self.assertTrue(self.principal.check_password(STRONG))

    def test_changing_the_password_keeps_you_signed_in_and_the_new_one_works(self):
        self.client.force_login(self.principal)
        self.assertRedirects(self.change_password(STRONG, CHOSEN), reverse("accounts:settings"))
        self.assertEqual(self.client.get(reverse("accounts:settings")).status_code, 200)

        self.client.logout()
        self.assertTrue(self.client.login(username="principal@riverbank.test", password=CHOSEN))


class TemporaryPasswordTests(AccountFixtures):
    def setUp(self):
        User.objects.filter(pk=self.owner.pk).update(must_change_password=True)
        self.owner.refresh_from_db()
        self.client.force_login(self.owner)

    def test_every_other_screen_sends_you_to_account_settings(self):
        for url in (reverse("core:school_dashboard"), reverse("students:student_list"),
                    reverse("messaging:index"), "/admin/"):
            with self.subTest(url=url):
                self.assertRedirects(
                    self.client.get(url), reverse("accounts:settings"), fetch_redirect_response=False
                )

    def test_the_page_says_why(self):
        self.assertContains(self.client.get(reverse("accounts:settings")), "temporary password")

    def test_signing_out_and_the_password_generator_still_work(self):
        self.assertEqual(self.client.post(reverse("accounts:generate_password")).status_code, 200)
        self.assertRedirects(self.client.post(reverse("logout")), reverse("core:landing"))

    def test_choosing_a_password_releases_you_to_your_dashboard(self):
        self.assertRedirects(
            self.change_password(STRONG, CHOSEN), reverse("core:school_dashboard"),
            fetch_redirect_response=False,
        )
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.must_change_password)
        self.assertEqual(self.client.get(reverse("core:school_dashboard")).status_code, 200)

    def test_resetting_by_email_also_ends_it(self):
        self.client.logout()
        self.client.post(reverse("password_reset"), {"email": self.owner.email})
        match = RESET_PATH.search(mail.outbox[0].body)
        form_page = self.client.get(match.group(), follow=True)
        self.client.post(
            form_page.redirect_chain[-1][0], {"new_password1": CHOSEN, "new_password2": CHOSEN}
        )
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.must_change_password)
        self.assertTrue(self.owner.check_password(CHOSEN))

    def test_setting_a_password_by_any_route_clears_the_flag(self):
        self.owner.set_password(CHOSEN)
        self.assertFalse(self.owner.must_change_password)
