"""The privacy policy: reachable, linked, and saying the things it must.

A privacy policy that 404s, or that exists but is linked from nowhere, is worse
than none at all -- it looks like compliance without being reachable by the
people it is written for. These tests are about reachability and the handful of
statements the NDPA turns into obligations, not about the prose.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.branding import PRIVACY_EMAIL, PRODUCT_NAME
from apps.core.roles import Role
from apps.core.views import PrivacyView
from apps.schools.models import Branch, School

User = get_user_model()


class PrivacyPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = School.all_objects.create(name="Fulfilled Academy")
        cls.branch = Branch.all_objects.create(school=cls.school, name="Main Campus")
        cls.owner = User.objects.create_user(
            "owner", email="owner@example.com", password="pw",
            role=Role.SCHOOL_OWNER, school=cls.school,
        )

    def test_a_stranger_can_read_it(self):
        response = self.client.get(reverse("core:privacy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Privacy policy")

    def test_a_signed_in_user_can_read_it_too(self):
        # base.html renders a different block for each case, so both are asked.
        self.client.force_login(self.owner)
        response = self.client.get(reverse("core:privacy"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Privacy policy")

    def test_it_is_not_noindexed(self):
        response = self.client.get(reverse("core:privacy"))
        self.assertNotContains(response, "noindex")
        self.assertNotIn("X-Robots-Tag", response.headers)

    def test_it_names_the_controller_processor_split(self):
        body = self.client.get(reverse("core:privacy")).content.decode()
        self.assertIn("NDPA", body)
        self.assertIn("data controller", body)
        self.assertIn("data processor", body)
        # The school is the controller, not us -- the whole basis of the page.
        self.assertIn("the school is the", body.lower())

    def test_it_covers_the_obligations_a_reader_came_for(self):
        body = self.client.get(reverse("core:privacy")).content.decode().lower()
        for phrase in [
            "how long we keep it",   # retention
            "your rights",           # access / correction / deletion
            "corrected",
            "deleted",
            "children",
            "cookies",
        ]:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, body)

    def test_it_gives_a_contact_address_that_is_not_the_no_reply_sender(self):
        from apps.core.branding import SUPPORT_EMAIL

        response = self.client.get(reverse("core:privacy"))
        self.assertContains(response, PRIVACY_EMAIL)
        self.assertNotEqual(PRIVACY_EMAIL, SUPPORT_EMAIL)

    def test_the_last_updated_date_is_fixed_rather_than_today(self):
        body = self.client.get(reverse("core:privacy")).content.decode()
        self.assertIn(PrivacyView.POLICY_UPDATED.strftime("%B %Y"), body)


class PrivacyIsLinkedTests(TestCase):
    """Reachable without knowing the URL."""

    def test_the_landing_footer_links_to_it(self):
        response = self.client.get(reverse("core:landing"))
        self.assertContains(response, reverse("core:privacy"))

    def test_the_signup_page_links_to_it(self):
        response = self.client.get(reverse("core:signup"))
        self.assertContains(response, reverse("core:privacy"))

    def test_it_is_in_the_sitemap(self):
        body = self.client.get("/sitemap.xml").content.decode()
        self.assertIn(f"{reverse('core:privacy')}</loc>", body)

    def test_it_is_in_llms_txt_with_the_ndpa_note(self):
        body = self.client.get("/llms.txt").content.decode()
        self.assertIn(reverse("core:privacy"), body)
        self.assertIn("NDPA", body)
        self.assertIn(PRODUCT_NAME, body)

    def test_robots_allows_it(self):
        body = self.client.get("/robots.txt").content.decode()
        self.assertIn(f"Allow: {reverse('core:privacy')}", body)
