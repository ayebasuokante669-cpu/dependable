"""Can each role actually *reach* the screens its sidebar offers?

The bug these guard against was not a 500 and not a leak. "Branches", "Staff"
and "Terms" pointed at Django admin URLs, so a proprietor -- whose account is
deliberately not ``is_staff`` -- clicked a link in their own sidebar and landed
on the admin login, on a site they were already signed in to. Nothing in the
suite noticed, because every test asked whether the *view* allowed the role,
and the view in question was Django's.

So these tests do what a person does: sign in, follow the link, and assert on
what comes back -- including that the link is not an admin URL.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.academics.models import Class, Level
from apps.core.navigation import nav_for
from apps.core.permissions import Capability, capabilities_for
from apps.core.roles import Role
from apps.fees.models import Term, TermSequence
from apps.schools.models import Branch, School

User = get_user_model()

#: Every screen the school owner must be able to open, by URL name.
OWNER_SCREENS = {
    "branches": "schools:branch_list",
    "staff": "staff:list",
    "classes": "academics:class_list",
    "terms": "fees:term_list",
    "fee structures": "fees:structure_list",
}


class RoleAccessTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = School.all_objects.create(name="Fulfilled Academy")
        cls.branch = Branch.all_objects.create(school=cls.school, name="Main Campus")
        cls.other_school = School.all_objects.create(name="Somewhere Else")
        cls.other_branch = Branch.all_objects.create(
            school=cls.other_school, name="Their Campus"
        )
        cls.owner = User.objects.create_user(
            "fulfilled-owner", email="owner@example.com", password="pw",
            role=Role.SCHOOL_OWNER, school=cls.school,
        )
        cls.principal = User.objects.create_user(
            "principal", email="principal@example.com", password="pw",
            role=Role.PRINCIPAL, school=cls.school, branch=cls.branch,
        )
        cls.bursar = User.objects.create_user(
            "bursar", email="bursar@example.com", password="pw",
            role=Role.BURSAR, school=cls.school, branch=cls.branch,
        )
        cls.platform = User.objects.create_superuser(
            "platform", email="platform@example.com", password="pw",
            role=Role.PLATFORM_OWNER,
        )
        cls.klass = Class.all_objects.create(
            school=cls.school, branch=cls.branch, name="Primary 1",
            level=Level.PRIMARY, year_in_level=1,
        )
        cls.term = Term.all_objects.create(
            school=cls.school, branch=cls.branch, name="First Term 2026/2027",
            academic_year="2026/2027", sequence=TermSequence.FIRST, is_current=True,
        )


class SchoolOwnerReachesTheirOwnSchoolTests(RoleAccessTestCase):
    """The reported bug, one test per screen."""

    def setUp(self):
        self.client.force_login(self.owner)

    def test_every_screen_answers_200(self):
        for label, url_name in OWNER_SCREENS.items():
            with self.subTest(screen=label):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)

    def test_no_screen_is_an_admin_url(self):
        # An admin URL would 302 to /admin/login/ for this account, which is
        # exactly how the bug presented.
        for label, url_name in OWNER_SCREENS.items():
            with self.subTest(screen=label):
                self.assertFalse(reverse(url_name).startswith("/admin/"))

    def test_the_sidebar_offers_those_screens_and_they_all_resolve(self):
        hrefs = {
            item.label: item
            for section in nav_for(
                Role.SCHOOL_OWNER,
                capabilities={c.value for c in capabilities_for(Role.SCHOOL_OWNER)},
            )
            for item in section.items
        }
        for label in ["Branches", "Staff", "Classes", "Terms", "Fee Structures"]:
            with self.subTest(entry=label):
                self.assertIn(label, hrefs)
                item = hrefs[label]
                self.assertTrue(item.available, f"{label} resolved to nothing")
                self.assertFalse(item.href.startswith("/admin/"), item.href)
                self.assertEqual(self.client.get(item.href).status_code, 200)

    def test_the_branch_list_shows_this_school_and_not_another(self):
        response = self.client.get(reverse("schools:branch_list"))
        self.assertContains(response, "Main Campus")
        self.assertNotContains(response, "Their Campus")

    def test_the_staff_list_shows_this_school_and_not_another(self):
        User.objects.create_user(
            "stranger", email="stranger@example.com", password="pw",
            role=Role.BURSAR, school=self.other_school, branch=self.other_branch,
        )
        response = self.client.get(reverse("staff:list"))
        self.assertContains(response, "fulfilled-owner")
        self.assertContains(response, "bursar")
        self.assertNotContains(response, "stranger")

    def test_they_can_add_a_branch_to_their_own_school(self):
        response = self.client.post(
            reverse("schools:branch_create"),
            {"name": "Second Campus", "city": "Port Harcourt", "state": "Rivers",
             "address": "", "head": "", "is_active": "on"},
        )
        self.assertEqual(response.status_code, 302)
        branch = Branch.all_objects.get(name="Second Campus")
        # Filed under their own school, which was never a form field.
        self.assertEqual(branch.school_id, self.school.id)

    def test_they_can_add_a_term(self):
        response = self.client.post(
            reverse("fees:term_create"),
            {"branch": self.branch.id, "name": "Second Term 2026/2027",
             "academic_year": "2026/2027", "sequence": TermSequence.SECOND,
             "due_date": ""},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Term.all_objects.filter(name="Second Term 2026/2027").exists()
        )


class FeeStructuresAreVisibleToBothOwnersTests(RoleAccessTestCase):
    """The client's explicit requirement: platform owner *and* school owner."""

    def test_both_owners_can_open_the_fee_structures_screen(self):
        for who, user in [("platform owner", self.platform), ("school owner", self.owner)]:
            with self.subTest(role=who):
                self.client.force_login(user)
                self.assertEqual(
                    self.client.get(reverse("fees:structure_list")).status_code, 200
                )

    def test_both_owners_hold_the_fee_capabilities(self):
        for role in [Role.PLATFORM_OWNER, Role.SCHOOL_OWNER]:
            with self.subTest(role=role):
                granted = capabilities_for(role)
                self.assertIn(Capability.VIEW_FEES, granted)
                self.assertIn(Capability.MANAGE_FEES, granted)


class OtherRolesKeepTheirLimitsTests(RoleAccessTestCase):
    """Widening the owner's access must not have widened everyone's."""

    def test_a_bursar_cannot_see_branches_or_staff(self):
        self.client.force_login(self.bursar)
        for url_name in ["schools:branch_list", "staff:list"]:
            with self.subTest(screen=url_name):
                self.assertEqual(self.client.get(reverse(url_name)).status_code, 403)

    def test_a_bursar_still_reads_fees_and_terms_without_editing_them(self):
        self.client.force_login(self.bursar)
        self.assertEqual(self.client.get(reverse("fees:structure_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("fees:term_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("fees:term_create")).status_code, 403)

    def test_a_principal_reads_branches_but_does_not_open_one(self):
        self.client.force_login(self.principal)
        self.assertEqual(self.client.get(reverse("schools:branch_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("staff:list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("schools:branch_create")).status_code, 403)

    def test_signed_out_visitors_are_sent_to_sign_in(self):
        for url_name in OWNER_SCREENS.values():
            with self.subTest(screen=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 302)
                self.assertIn("/accounts/login/", response["Location"])

    def test_another_schools_branch_is_a_404_not_a_403(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("schools:branch_update", args=[self.other_branch.id])
        )
        self.assertEqual(response.status_code, 404)
