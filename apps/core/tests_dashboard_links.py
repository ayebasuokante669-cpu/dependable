"""Every summary on a dashboard is a link to the page that answers it in full.

And the product's tagline, which the landing page, the sign-in panel and the
footer all read from one constant.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.branding import PRODUCT_SUBHEAD, PRODUCT_TAGLINE
from apps.core.roles import Role
from apps.payments.tests import PaymentsTestCase

User = get_user_model()


def href(name: str, query: str = "") -> str:
    return f'href="{reverse(name)}{query}"'


class DashboardLinkTests(PaymentsTestCase):
    def dashboard(self, user, name):
        self.client.force_login(user)
        response = self.client.get(reverse(name))
        self.assertEqual(response.status_code, 200)
        return response

    def assert_tile_links(self, response, links):
        for link in links:
            with self.subTest(link=link):
                # The whole card is the anchor, not a small link inside it.
                self.assertContains(response, f'<a {link}')
                self.assertContains(response, link)

    def test_owner_dashboard(self):
        response = self.dashboard(self.owner, "core:school_dashboard")
        self.assert_tile_links(response, [
            href("schools:branch_list"),
            href("students:student_list"),
            href("academics:class_list"),
            href("payments:outstanding"),
            href("reports:index"),
        ])

    def test_principal_dashboard(self):
        response = self.dashboard(self.principal, "core:branch_dashboard")
        self.assert_tile_links(response, [
            href("students:student_list"),
            href("academics:class_list"),
            href("academics:subject_list"),
            href("payments:outstanding"),
            href("reports:index"),
        ])

    def test_bursar_dashboard(self):
        response = self.dashboard(self.bursar, "core:bursar_dashboard")
        self.assert_tile_links(response, [
            href("fees:structure_list"),
            href("payments:index", "?status=confirmed"),
            href("payments:index"),
            href("payments:outstanding"),
            href("reports:index"),
        ])

    def test_every_link_opens_for_the_role_it_is_shown_to(self):
        cases = [
            (self.owner, ["schools:branch_list", "students:student_list",
                          "academics:class_list", "payments:outstanding",
                          "reports:index"]),
            (self.principal, ["students:student_list", "academics:class_list",
                              "academics:subject_list", "payments:outstanding",
                              "reports:index"]),
            (self.bursar, ["fees:structure_list", "payments:index",
                           "payments:outstanding", "reports:index"]),
        ]
        for user, names in cases:
            self.client.force_login(user)
            for name in names:
                with self.subTest(user=user.username, page=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_no_link_is_nested_inside_another(self):
        for user, name in (
            (self.owner, "core:school_dashboard"),
            (self.principal, "core:branch_dashboard"),
            (self.bursar, "core:bursar_dashboard"),
        ):
            with self.subTest(dashboard=name):
                html = self.dashboard(user, name).content.decode()
                depth, worst = 0, 0
                i = 0
                while True:
                    opening = html.find("<a ", i)
                    closing = html.find("</a>", i)
                    if closing == -1:
                        break
                    if opening != -1 and opening < closing:
                        depth += 1
                        worst = max(worst, depth)
                        i = opening + 3
                    else:
                        depth -= 1
                        i = closing + 4
                self.assertEqual(worst, 1)


class PlatformDashboardLinkTests(TestCase):
    def test_every_count_links_to_the_schools_grid_or_accounts(self):
        owner = User.objects.create_user(
            "platform", password="pw", role=Role.PLATFORM_OWNER
        )
        self.client.force_login(owner)
        response = self.client.get(reverse("core:platform_overview"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        grid = href("core:platform_schools")
        self.assertGreaterEqual(html.count(f"<a {grid}"), 3)
        self.assertIn(f"<a {href('staff:list')}", html)


class TaglineTests(TestCase):
    OLD = "School management that starts with the fees"

    def test_the_tagline_and_subhead(self):
        self.assertEqual(PRODUCT_TAGLINE, "Run Your School. Not Your Paperwork.")
        self.assertEqual(
            PRODUCT_SUBHEAD,
            "Track school fees, manage student records and communicate with "
            "parents, all in one place.",
        )

    def test_public_pages_carry_the_new_tagline_and_not_the_old(self):
        for path in ("/", "/accounts/login/", "/signup/", "/about/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertContains(response, PRODUCT_TAGLINE)
                self.assertNotContains(response, self.OLD)
                self.assertNotContains(response, "Know what every child owes")
                # It ends in its own full stop; nothing adds a second.
                self.assertNotContains(response, "Paperwork..")

    def test_the_landing_hero_pairs_it_with_the_subhead(self):
        response = self.client.get("/")
        self.assertContains(response, PRODUCT_SUBHEAD)
