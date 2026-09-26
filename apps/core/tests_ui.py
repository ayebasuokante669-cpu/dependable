"""The navigation and settings pass: what each fix must keep doing.

None of this is about how a screen looks -- a test cannot see a pulse or judge a
transition. What it can hold onto is everything underneath the visual work that
would be easy to break later:

* a person can change their own display name and job title, and cannot change
  their own role while they are at it;
* the logo is the proprietor's, in the form as well as on the page;
* "School settings" is gone from the one sidebar it 404'd in, and still present in
  the two where it works;
* the footer has no link that goes nowhere, and About is a real page in the
  sitemap;
* the platform's Users screen shows what a school's Staff screen does not, and
  deactivation locks an account out without deleting anything.

The animation work -- the toast sequence, the status-dot pulse, the collapsed rail
-- is asserted only where it has a structural footprint: the classes the
stylesheet keys off, and the stage constants the script and the stylesheet have to
agree on. A drifting duration is the failure mode those have, and it is the one a
test can catch.
"""

from __future__ import annotations

import pathlib
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.core.modules import ALL_KEYS
from apps.core.navigation import nav_for
from apps.core.permissions import Capability, capabilities_for
from apps.core.roles import Role
from apps.schools.models import Branch, School

User = get_user_model()

#: A 1x1 PNG -- enough to exercise an upload without committing an image.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "000557bfabd40000000049454e44ae426082"
)


def png(name="logo.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PNG_1PX, content_type="image/png")


class UITestCase(TestCase):
    """One school with every role, and a second school to stay out of."""

    @classmethod
    def setUpTestData(cls):
        cls.school = School.all_objects.create(name="Fulfilled Academy")
        cls.main = Branch.all_objects.create(school=cls.school, name="Main Campus")
        cls.other = School.all_objects.create(name="Rival College")
        cls.other_branch = Branch.all_objects.create(
            school=cls.other, name="West Campus"
        )

        cls.owner = User.objects.create_user(
            "owner", email="owner@example.com", password="pw",
            first_name="Ada", last_name="Owner",
            role=Role.SCHOOL_OWNER, school=cls.school,
        )
        cls.principal = User.objects.create_user(
            "principal", email="principal@example.com", password="pw",
            role=Role.PRINCIPAL, school=cls.school, branch=cls.main,
        )
        cls.bursar = User.objects.create_user(
            "bursar", email="bursar@example.com", password="pw",
            role=Role.BURSAR, school=cls.school, branch=cls.main,
        )
        cls.platform = User.objects.create_superuser(
            "platform", email="platform@example.com", password="pw",
            role=Role.PLATFORM_OWNER,
        )
        cls.stranger = User.objects.create_user(
            "stranger", email="stranger@example.com", password="pw",
            role=Role.BURSAR, school=cls.other, branch=cls.other_branch,
        )

    def labels_for(self, role) -> set[str]:
        return {
            item.label
            for section in nav_for(
                role, "/", {c.value for c in capabilities_for(role)}, ALL_KEYS
            )
            for item in section.items
        }


# ===========================================================================
# Your own name and job title
# ===========================================================================


class ProfileTests(UITestCase):
    def setUp(self):
        self.client.force_login(self.owner)
        self.url = reverse("accounts:settings")

    def test_the_page_offers_the_fields(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'name="first_name"')
        self.assertContains(response, 'name="last_name"')
        self.assertContains(response, 'name="job_title"')

    def test_saving_changes_how_you_are_named(self):
        response = self.client.post(self.url, {
            "form": "profile", "first_name": "Chidi", "last_name": "Eze",
            "job_title": "Finance Officer",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.get_full_name(), "Chidi Eze")
        self.assertEqual(self.owner.job_title, "Finance Officer")

    def test_the_new_name_reaches_the_sidebar(self):
        self.client.post(self.url, {
            "form": "profile", "first_name": "Chidi", "last_name": "Eze",
            "job_title": "Finance Officer",
        })
        response = self.client.get(reverse("core:school_dashboard"))
        self.assertContains(response, "Chidi Eze")
        self.assertContains(response, "Finance Officer")

    def test_whitespace_is_tidied_rather_than_stored(self):
        self.client.post(self.url, {
            "form": "profile", "first_name": "  Chidi  ",
            "last_name": " Eze ", "job_title": "  Head   of  Maths ",
        })
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, "Chidi")
        self.assertEqual(self.owner.last_name, "Eze")
        self.assertEqual(self.owner.job_title, "Head of Maths")

    def test_a_blank_name_is_refused(self):
        """The shell falls back to the username, which is derived from an email
        address and reads like one."""
        response = self.client.post(self.url, {
            "form": "profile", "first_name": "", "last_name": "", "job_title": "",
        })
        self.assertEqual(response.status_code, 200)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, "Ada")

    def test_the_job_title_may_be_emptied(self):
        """Free text, and not everybody has one."""
        self.client.post(self.url, {
            "form": "profile", "first_name": "Ada", "last_name": "Owner",
            "job_title": "",
        })
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.job_title, "")

    def test_you_cannot_promote_yourself(self):
        """Role, school and campus decide what an account can see, so they are
        not on a form the account fills in for itself."""
        response = self.client.post(self.url, {
            "form": "profile", "first_name": "Ada", "last_name": "Owner",
            "job_title": "", "role": Role.PLATFORM_OWNER,
            "school": self.other.pk, "is_superuser": "on",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.role, Role.SCHOOL_OWNER)
        self.assertEqual(self.owner.school_id, self.school.pk)
        self.assertFalse(self.owner.is_superuser)

    def test_every_role_can_edit_their_own(self):
        for who in (self.owner, self.principal, self.bursar, self.platform):
            with self.subTest(user=who.username):
                self.client.force_login(who)
                response = self.client.post(self.url, {
                    "form": "profile", "first_name": "Given",
                    "last_name": "Family", "job_title": "Role",
                }, follow=True)
                self.assertEqual(response.status_code, 200)
                who.refresh_from_db()
                self.assertEqual(who.get_full_name(), "Given Family")

    def test_a_rejected_profile_does_not_wipe_the_other_forms(self):
        """Three forms on one page, told apart by the hidden `form` field."""
        response = self.client.post(self.url, {
            "form": "profile", "first_name": "", "last_name": "Eze",
        })
        self.assertContains(response, 'name="old_password"')
        self.assertContains(response, 'name="email"')


# ===========================================================================
# The school logo
# ===========================================================================


class LogoCapabilityTests(UITestCase):
    def test_only_the_proprietor_and_the_platform_hold_it(self):
        self.assertIn(
            Capability.MANAGE_SCHOOL_LOGO, capabilities_for(Role.SCHOOL_OWNER)
        )
        self.assertIn(
            Capability.MANAGE_SCHOOL_LOGO, capabilities_for(Role.PLATFORM_OWNER)
        )
        for role in (Role.PRINCIPAL, Role.BURSAR):
            with self.subTest(role=role):
                self.assertNotIn(
                    Capability.MANAGE_SCHOOL_LOGO, capabilities_for(role)
                )

    def test_a_principal_still_manages_the_rest_of_the_profile(self):
        """They correct a misspelled school name; they do not choose the mark
        that goes on every receipt."""
        self.assertIn(
            Capability.MANAGE_SCHOOL_PROFILE, capabilities_for(Role.PRINCIPAL)
        )


class LogoScreenTests(UITestCase):
    def setUp(self):
        self.url = reverse("core:school_settings")

    def test_the_proprietor_is_offered_the_control(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertContains(response, "Add your school logo")
        self.assertContains(response, 'class="logo-file"')

    def test_a_principal_is_told_whose_decision_it_is(self):
        self.client.force_login(self.principal)
        response = self.client.get(self.url)
        self.assertContains(response, "the proprietor's decision")
        self.assertNotContains(response, 'class="logo-file"')

    def test_the_form_drops_the_field_rather_than_disabling_it(self):
        from apps.core.forms import SchoolProfileForm

        allowed = SchoolProfileForm(instance=self.school, can_manage_logo=True)
        refused = SchoolProfileForm(instance=self.school, can_manage_logo=False)
        self.assertIn("logo", allowed.fields)
        self.assertIn("remove_logo", allowed.fields)
        self.assertNotIn("logo", refused.fields)
        self.assertNotIn("remove_logo", refused.fields)

    def test_the_proprietor_can_upload_one(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, {
            "name": "Fulfilled Academy", "contact_email": "",
            "contact_phone": "", "logo": png(),
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.school.refresh_from_db()
        self.assertTrue(self.school.logo)

    def test_a_principal_cannot_even_by_posting_one(self):
        """The field is absent from their form, so an upload posted by hand has
        nowhere to land."""
        self.client.force_login(self.principal)
        response = self.client.post(self.url, {
            "name": "Fulfilled Academy", "contact_email": "",
            "contact_phone": "", "logo": png(),
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.school.refresh_from_db()
        self.assertFalse(self.school.logo)

    def test_a_principal_can_still_save_the_rest(self):
        self.client.force_login(self.principal)
        self.client.post(self.url, {
            "name": "Fulfilled Academy", "contact_email": "office@fa.example",
            "contact_phone": "08031234567",
        })
        self.school.refresh_from_db()
        self.assertEqual(self.school.contact_email, "office@fa.example")

    def test_the_proprietor_can_remove_one(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {
            "name": "Fulfilled Academy", "contact_email": "",
            "contact_phone": "", "logo": png(),
        })
        self.school.refresh_from_db()
        self.assertTrue(self.school.logo)

        self.client.post(self.url, {
            "name": "Fulfilled Academy", "contact_email": "",
            "contact_phone": "", "remove_logo": "on",
        })
        self.school.refresh_from_db()
        self.assertFalse(self.school.logo)

    def test_a_new_file_wins_over_the_remove_tick(self):
        """Somebody who chose a replacement and left the box ticked meant to
        replace it -- the other reading throws away the upload."""
        self.client.force_login(self.owner)
        self.client.post(self.url, {
            "name": "Fulfilled Academy", "contact_email": "",
            "contact_phone": "", "logo": png("first.png"),
        })
        self.client.post(self.url, {
            "name": "Fulfilled Academy", "contact_email": "",
            "contact_phone": "", "logo": png("second.png"), "remove_logo": "on",
        })
        self.school.refresh_from_db()
        self.assertTrue(self.school.logo)
        self.assertIn("second", self.school.logo.name)


class LogoPromptTests(UITestCase):
    """The signpost on the dashboard, which is what people could not find."""

    def test_it_is_shown_to_the_proprietor_while_there_is_no_logo(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("core:school_dashboard"))
        self.assertContains(response, "Add your school logo")
        self.assertContains(response, f"{reverse('core:school_settings')}#logo")

    def test_it_goes_once_a_logo_is_uploaded(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("core:school_settings"), {
            "name": "Fulfilled Academy", "contact_email": "",
            "contact_phone": "", "logo": png(),
        })
        response = self.client.get(reverse("core:school_dashboard"))
        self.assertNotContains(response, "Add your school logo")

    def test_a_principal_is_not_prompted_for_something_they_cannot_do(self):
        self.client.force_login(self.principal)
        response = self.client.get(reverse("core:branch_dashboard"))
        self.assertNotContains(response, "Add your school logo")


# ===========================================================================
# School settings in the sidebar
# ===========================================================================


class SchoolSettingsNavTests(UITestCase):
    def test_the_platform_owner_is_not_offered_it(self):
        self.assertNotIn("School settings", self.labels_for(Role.PLATFORM_OWNER))

    def test_the_school_side_roles_still_are(self):
        for role in (Role.SCHOOL_OWNER, Role.PRINCIPAL):
            with self.subTest(role=role):
                self.assertIn("School settings", self.labels_for(role))

    def test_a_bursar_never_was(self):
        self.assertNotIn("School settings", self.labels_for(Role.BURSAR))

    def test_the_platform_owner_reaches_a_school_profile_from_schools(self):
        self.client.force_login(self.platform)
        overview = self.client.get(reverse("core:platform_overview"))
        detail_url = reverse("core:platform_school", args=[self.school.pk])
        self.assertContains(overview, detail_url)

        detail = self.client.get(detail_url)
        self.assertContains(detail, 'name="name"')
        self.assertContains(detail, 'class="logo-file"')

    def test_the_platform_owner_can_edit_any_schools_profile(self):
        self.client.force_login(self.platform)
        response = self.client.post(
            reverse("core:platform_school_profile", args=[self.other.pk]),
            {"name": "Rival College Renamed",
             "contact_email": "office@rival.example", "contact_phone": ""},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.other.refresh_from_db()
        self.assertEqual(self.other.name, "Rival College Renamed")

    def test_a_school_owner_cannot_use_that_route(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("core:platform_school_profile", args=[self.school.pk]),
            {"name": "Hacked", "contact_email": "", "contact_phone": ""},
        )
        self.assertEqual(response.status_code, 403)
        self.school.refresh_from_db()
        self.assertEqual(self.school.name, "Fulfilled Academy")

    def test_that_route_has_nothing_to_get(self):
        self.client.force_login(self.platform)
        response = self.client.get(
            reverse("core:platform_school_profile", args=[self.school.pk])
        )
        self.assertRedirects(
            response, reverse("core:platform_school", args=[self.school.pk])
        )

    def test_a_rejected_form_comes_back_on_the_whole_page(self):
        """Not a stub: the campuses and the module switches are still around it."""
        self.client.force_login(self.platform)
        response = self.client.post(
            reverse("core:platform_school_profile", args=[self.school.pk]),
            {"name": "", "contact_email": "", "contact_phone": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Campuses")
        self.assertContains(response, "Features")
        self.assertContains(response, "Main Campus")


# ===========================================================================
# The footer and the About page
# ===========================================================================


class FooterTests(UITestCase):
    def body(self, url) -> str:
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()

    def test_careers_is_gone(self):
        self.assertNotIn("Careers", self.body(reverse("core:landing")))

    def test_contact_points_at_the_socials_in_the_same_footer(self):
        body = self.body(reverse("core:landing"))
        self.assertIn('href="#contact"', body)
        self.assertIn('id="contact"', body)
        self.assertIn("Get in touch", body)

    def test_about_is_linked(self):
        self.assertIn(reverse("core:about"), self.body(reverse("core:landing")))

    def test_no_footer_link_goes_nowhere(self):
        """Every href in the Company and About columns resolves. The social
        icons are the documented exception -- real links with real labels, whose
        hrefs the product's own accounts will replace."""
        body = self.body(reverse("core:landing"))
        footer = body[body.index("<footer"):]
        columns = footer[: footer.index("Social accounts")] + footer[
            footer.index("</ul>", footer.index("Social accounts")):
        ]
        dead = re.findall(r'class="footer-link"[^>]*href="#"', columns)
        dead += re.findall(r'href="#"[^>]*class="footer-link"', columns)
        self.assertEqual(dead, [])


class AboutPageTests(UITestCase):
    def test_it_is_public(self):
        response = self.client.get(reverse("core:about"))
        self.assertEqual(response.status_code, 200)

    def test_it_reads_inside_the_shell_too(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("core:about"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Why we start with the fees")

    def test_it_describes_the_product_from_the_one_source(self):
        from apps.core.branding import PRODUCT_DESCRIPTION
        from apps.core.seo import FEATURES

        response = self.client.get(reverse("core:about"))
        self.assertContains(response, PRODUCT_DESCRIPTION)
        for title, _ in FEATURES:
            with self.subTest(feature=title):
                self.assertContains(response, title)

    def test_it_is_in_the_sitemap_and_llms_txt(self):
        """A new public page has to be declared in apps/core/seo.py, or
        tests_seo's URLconf walk fails it as neither public nor private."""
        self.assertContains(self.client.get(reverse("sitemap")), "/about/")
        self.assertContains(self.client.get(reverse("llms_txt")), "/about/")

    def test_it_has_its_own_title_and_description(self):
        response = self.client.get(reverse("core:about"))
        self.assertContains(response, "<title>About SCHOOLCORD</title>")
        self.assertContains(response, 'name="description"')

    def test_it_links_on_to_the_privacy_policy_and_signup(self):
        response = self.client.get(reverse("core:about"))
        self.assertContains(response, reverse("core:privacy"))
        self.assertContains(response, reverse("core:signup"))


# ===========================================================================
# Staff, and Users
# ===========================================================================


class UsersScreenTests(UITestCase):
    def test_the_platform_sidebar_says_users(self):
        labels = self.labels_for(Role.PLATFORM_OWNER)
        self.assertIn("Users", labels)
        self.assertNotIn("Staff", labels)

    def test_a_schools_sidebar_still_says_staff(self):
        for role in (Role.SCHOOL_OWNER, Role.PRINCIPAL):
            with self.subTest(role=role):
                labels = self.labels_for(role)
                self.assertIn("Staff", labels)
                self.assertNotIn("Users", labels)

    def test_both_entries_point_at_the_same_screen(self):
        for role in (Role.PLATFORM_OWNER, Role.SCHOOL_OWNER):
            with self.subTest(role=role):
                hrefs = {
                    item.label: item.href
                    for section in nav_for(
                        role, "/",
                        {c.value for c in capabilities_for(role)}, ALL_KEYS,
                    )
                    for item in section.items
                }
                label = "Users" if role == Role.PLATFORM_OWNER else "Staff"
                self.assertEqual(hrefs[label], reverse("staff:list"))

    def table_headings(self, response) -> list[str]:
        """The column headings, and only those.

        Read out of the table rather than looked for in the page: the sidebar has
        a nav *section* called "School", so a bare search for the word finds it in
        both scopes and proves nothing.
        """
        body = response.content.decode()
        table = body[body.index("<table"): body.index("</thead>")]
        return [
            re.sub(r"\s+", " ", cell).strip()
            # `<th\s` and not `<th`, or the pattern matches `<thead` too and the
            # first "heading" comes back as the whole opening row.
            for cell in re.findall(r"<th\s[^>]*>(.*?)</th>", table, re.S)
        ]

    def test_the_platform_screen_shows_the_school_column(self):
        self.client.force_login(self.platform)
        response = self.client.get(reverse("staff:list"))
        self.assertIn("School", self.table_headings(response))
        self.assertTrue(response.context["is_platform"])
        # And every school's accounts, which is what the column is for.
        self.assertContains(response, "Rival College")
        self.assertContains(response, "stranger@example.com")

    def test_a_schools_screen_does_not(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("staff:list"))
        self.assertNotIn("School", self.table_headings(response))
        self.assertFalse(response.context["is_platform"])

    def test_a_school_sees_only_its_own_accounts(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("staff:list"))
        self.assertContains(response, "owner@example.com")
        self.assertNotContains(response, "stranger@example.com")

    def test_the_columns_asked_for_are_all_there(self):
        self.client.force_login(self.platform)
        headings = self.table_headings(self.client.get(reverse("staff:list")))
        for heading in ("Name", "Job title", "School", "Campus", "Role",
                        "Status", "Actions"):
            with self.subTest(column=heading):
                self.assertIn(heading, headings)

    def test_it_is_titled_for_its_scope(self):
        self.client.force_login(self.platform)
        self.assertEqual(
            self.client.get(reverse("staff:list")).context["page_title"], "Users"
        )
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.get(reverse("staff:list")).context["page_title"], "Staff"
        )

    def test_invitation_is_offered_on_both(self):
        for who in (self.platform, self.owner):
            with self.subTest(user=who.username):
                self.client.force_login(who)
                response = self.client.get(reverse("staff:list"))
                self.assertContains(response, reverse("staff:create"))
                self.assertContains(response, reverse("staff:bulk_create"))


class DeactivationTests(UITestCase):
    def activate(self, person, on: bool):
        data = {"active": "on"} if on else {}
        return self.client.post(
            reverse("staff:activation", args=[person.pk]), data, follow=True
        )

    def test_only_the_platform_holds_the_capability(self):
        self.assertIn(
            Capability.DEACTIVATE_ACCOUNTS, capabilities_for(Role.PLATFORM_OWNER)
        )
        for role in (Role.SCHOOL_OWNER, Role.PRINCIPAL, Role.BURSAR):
            with self.subTest(role=role):
                self.assertNotIn(
                    Capability.DEACTIVATE_ACCOUNTS, capabilities_for(role)
                )

    def test_the_platform_owner_can_deactivate(self):
        self.client.force_login(self.platform)
        response = self.activate(self.bursar, False)
        self.assertEqual(response.status_code, 200)
        self.bursar.refresh_from_db()
        self.assertFalse(self.bursar.is_active)

    def test_a_deactivated_account_cannot_sign_in(self):
        self.client.force_login(self.platform)
        self.activate(self.bursar, False)
        self.assertFalse(
            self.client_class().login(username="bursar@example.com", password="pw")
        )

    def test_reactivating_restores_access_with_the_same_password(self):
        self.client.force_login(self.platform)
        self.activate(self.bursar, False)
        self.activate(self.bursar, True)
        self.bursar.refresh_from_db()
        self.assertTrue(self.bursar.is_active)
        self.assertTrue(
            self.client_class().login(username="bursar@example.com", password="pw")
        )

    def test_it_deletes_nothing(self):
        self.client.force_login(self.platform)
        self.activate(self.bursar, False)
        self.assertTrue(User.objects.filter(pk=self.bursar.pk).exists())
        self.bursar.refresh_from_db()
        self.assertEqual(self.bursar.email, "bursar@example.com")
        self.assertEqual(self.bursar.school_id, self.school.pk)

    def test_a_school_owner_cannot_deactivate_anyone(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("staff:activation", args=[self.bursar.pk]), {}
        )
        self.assertEqual(response.status_code, 403)
        self.bursar.refresh_from_db()
        self.assertTrue(self.bursar.is_active)

    def test_a_principal_and_a_bursar_cannot_either(self):
        for who in (self.principal, self.bursar):
            with self.subTest(user=who.username):
                self.client.force_login(who)
                self.assertEqual(
                    self.client.post(
                        reverse("staff:activation", args=[self.owner.pk]), {}
                    ).status_code,
                    403,
                )
                self.owner.refresh_from_db()
                self.assertTrue(self.owner.is_active)

    def test_you_cannot_deactivate_yourself(self):
        """A footgun rather than a permission question: switching off the account
        you are signed in as locks you out of the screen that switches it back."""
        self.client.force_login(self.platform)
        self.activate(self.platform, False)
        self.platform.refresh_from_db()
        self.assertTrue(self.platform.is_active)

    def test_the_control_is_not_drawn_against_your_own_row(self):
        self.client.force_login(self.platform)
        response = self.client.get(reverse("staff:list"))
        self.assertNotContains(
            response, reverse("staff:activation", args=[self.platform.pk])
        )
        self.assertContains(
            response, reverse("staff:activation", args=[self.bursar.pk])
        )

    def test_a_school_screen_draws_no_deactivate_control_at_all(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("staff:list"))
        self.assertNotContains(
            response, reverse("staff:activation", args=[self.bursar.pk])
        )

    def test_it_is_never_a_get(self):
        self.client.force_login(self.platform)
        response = self.client.get(
            reverse("staff:activation", args=[self.bursar.pk])
        )
        self.assertEqual(response.status_code, 405)
        self.bursar.refresh_from_db()
        self.assertTrue(self.bursar.is_active)

    def test_a_signed_out_visitor_is_sent_to_sign_in(self):
        response = self.client.post(
            reverse("staff:activation", args=[self.bursar.pk]), {}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
        self.bursar.refresh_from_db()
        self.assertTrue(self.bursar.is_active)

    def test_an_account_that_does_not_exist_is_a_404(self):
        self.client.force_login(self.platform)
        response = self.client.post(reverse("staff:activation", args=[99999]), {})
        self.assertEqual(response.status_code, 404)


# ===========================================================================
# The moving parts, where they have a structural footprint
# ===========================================================================


class MotionContractTests(TestCase):
    """The stylesheet and the script have to agree about the toast's stages.

    A test cannot see a transition. What it can see is the two files drifting
    apart, which is the failure mode this animation actually has: ui.js decides
    when each stage starts and app.css decides how long it takes, and a stage cut
    short is exactly the jank that was reported.
    """

    CSS = pathlib.Path(settings.BASE_DIR) / "assets" / "app.css"
    JS = pathlib.Path(settings.BASE_DIR) / "static" / "js" / "ui.js"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = cls.CSS.read_text(encoding="utf-8")
        cls.js = cls.JS.read_text(encoding="utf-8")

    def css_ms(self, token: str) -> int:
        match = re.search(rf"--{token}:\s*(\d+)ms", self.css)
        self.assertIsNotNone(match, f"--{token} is not declared in app.css")
        return int(match.group(1))

    def js_ms(self, name: str) -> int:
        match = re.search(rf"var {name} = (\d+);", self.js)
        self.assertIsNotNone(match, f"{name} is not declared in ui.js")
        return int(match.group(1))

    def test_each_stage_is_given_at_least_as_long_as_it_takes(self):
        for token, constant in (
            ("toast-fade", "TOAST_FADE_MS"),
            ("toast-open", "TOAST_OPEN_MS"),
            ("toast-rise", "TOAST_RISE_MS"),
        ):
            with self.subTest(stage=token):
                self.assertGreaterEqual(
                    self.js_ms(constant),
                    self.css_ms(token),
                    f"ui.js starts the next stage before --{token} has finished",
                )

    def test_the_toast_reads_rather_than_flashes(self):
        """The reported complaint was that it was too fast. Each stage is now
        slower than the design system's general-purpose 'settle'."""
        settle = int(re.search(r"--duration-settle:\s*(\d+)ms", self.css).group(1))
        for token in ("toast-rise", "toast-open"):
            with self.subTest(stage=token):
                self.assertGreater(self.css_ms(token), settle)

    def test_the_words_fade_separately_from_the_track(self):
        """The crushed-text fix. The message has its own opacity, keyed off a
        class the script adds last and removes first."""
        self.assertIn(".toast.is-lit .toast-panel > div", self.css)
        self.assertIn(".toast-panel > div", self.css)
        # Removed before the panel closes, added after it opens.
        exit_order = self.js.index("el.classList.remove('is-lit')")
        collapse = self.js.index("el.classList.remove('is-open')")
        self.assertLess(exit_order, collapse)
        entry_open = self.js.index("el.classList.add('is-open')")
        entry_lit = self.js.index("el.classList.add('is-lit')")
        self.assertLess(entry_open, entry_lit)

    def test_the_panel_no_longer_springs(self):
        """A spring overshoots, and overshooting a width makes the track spring
        past its content and snap back.

        Asserted on the declaration rather than on the block, because the block
        also carries a comment explaining which easing it is *not* using.
        """
        panel = self.css[self.css.index(".toast-panel {"):]
        panel = panel[: panel.index("}")]
        declaration = re.search(r"transition:\s*grid-template-columns[^;]*;", panel)
        self.assertIsNotNone(declaration, "the panel has no width transition")
        self.assertIn("--toast-open", declaration.group(0))
        self.assertNotIn("ease-spring", declaration.group(0))

    def test_a_server_rendered_toast_is_readable_without_the_script(self):
        template = (
            pathlib.Path(settings.BASE_DIR)
            / "templates" / "partials" / "_toast.html"
        ).read_text(encoding="utf-8")
        self.assertIn("is-in is-open is-lit", template)
        # And the script takes all three off before replaying the entrance.
        self.assertIn("remove('is-in', 'is-open', 'is-lit')", self.js)

    def test_the_status_dot_pulse_is_compositor_friendly_and_opt_out(self):
        """Transform and opacity, not box-shadow: a legend or a long table can
        have a couple of dozen dots animating at once."""
        keyframes = self.css[self.css.index("@keyframes status-pulse"):]
        keyframes = keyframes[: keyframes.index("\n}")]
        self.assertIn("transform:", keyframes)
        self.assertIn("opacity:", keyframes)
        self.assertNotIn("box-shadow", keyframes)
        # Ends transparent, so the global reduced-motion cut leaves no ring.
        self.assertRegex(keyframes, r"100%\s*\{[^}]*opacity:\s*0")
        self.assertIn(':root[data-perf="lite"] .status-dot::after', self.css)

    def test_reduced_motion_is_honoured_globally(self):
        """One opt-out the pulse and the toast both fall under."""
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.css)
        self.assertIn("animation-iteration-count: 1 !important", self.css)

    def test_the_sidebar_toggle_no_longer_rotates(self):
        """It is a hamburger now, not a chevron that had to flip."""
        self.assertNotIn(
            ':root[data-sidebar="collapsed"] .sidebar-toggle svg', self.css
        )
        header = (
            pathlib.Path(settings.BASE_DIR)
            / "templates" / "partials" / "_sidebar_header.html"
        ).read_text(encoding="utf-8")
        # The three-bar path, twice -- once per toggle.
        self.assertEqual(header.count("M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5"), 2)

    def test_the_collapsed_rail_drops_its_padding_to_one_axis(self):
        """The cramped/misaligned collapsed state: the header, the nav and the
        footer each centred their glyphs on a different line because each kept
        its own expanded padding."""
        self.assertIn("--sidebar-pad", self.css)
        self.assertRegex(
            self.css,
            r':root\[data-sidebar="collapsed"\]\s*\{[^}]*--sidebar-pad:\s*0',
        )
        for name in ("_sidebar_header.html", "_nav.html", "_sidebar_footer.html"):
            path = (
                pathlib.Path(settings.BASE_DIR) / "templates" / "partials" / name
            )
            with self.subTest(template=name):
                self.assertIn("sidebar-pad", path.read_text(encoding="utf-8"))
