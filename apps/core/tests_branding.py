"""Tests for the product's identity, and for the class of bug that hid it.

The comment leak these guard against is nasty precisely because nothing fails:
a wrapped `{# ... #}` is valid template source, renders without error, and
simply prints developer prose onto the page. No exception, no 500, no failing
assertion anywhere -- just a login form telling a customer what Django does
with bad passwords. The only way to catch it is to look at the source and at
the finished HTML, which is what the first two test classes do.
"""

from __future__ import annotations

import pathlib
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.core.branding import PRODUCT_NAME
from apps.core.roles import Role
from apps.schools.models import Branch, School

User = get_user_model()

TEMPLATE_ROOT = pathlib.Path(settings.BASE_DIR) / "templates"

#: A 1x1 PNG -- enough to exercise the upload without committing an image.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "000557bfabd40000000049454e44ae426082"
)


class TemplateCommentSyntaxTests(TestCase):
    """`{# #}` is single-line only. A wrapped one is not a comment at all."""

    def test_no_template_has_a_multi_line_hash_comment(self):
        offenders = []
        for path in sorted(TEMPLATE_ROOT.rglob("*.html")):
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                for match in re.finditer(r"\{#", line):
                    if "#}" not in line[match.end():]:
                        offenders.append(
                            f"{path.relative_to(TEMPLATE_ROOT).as_posix()}:{number}"
                        )
                        break

        self.assertEqual(
            offenders,
            [],
            "These `{# #}` comments wrap onto a second line, which Django does "
            "not treat as a comment -- they render to the page as text. Use "
            "{% comment %}...{% endcomment %} instead:\n  "
            + "\n  ".join(offenders),
        )


class RenderedPageTests(TestCase):
    """What actually reaches the browser, on the pages a stranger can see."""

    #: The pilot tenant is genuinely called this; it is not a leaked brand.
    ALLOWED = ("Fulfilled Academy",)

    def body(self, url) -> str:
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        html = response.content.decode()
        html = re.sub(r"(?s)<script\b.*?</script>", "", html)
        html = re.sub(r"(?s)<style\b.*?</style>", "", html)
        for term in self.ALLOWED:
            html = html.replace(term, "")
        return html

    def public_pages(self):
        return [
            reverse("core:landing"),
            reverse("login"),
            reverse("core:signup"),
            reverse("password_reset"),
        ]

    def test_no_public_page_leaks_template_syntax(self):
        for url in self.public_pages():
            with self.subTest(url=url):
                body = self.body(url)
                self.assertNotIn("{#", body)
                self.assertNotIn("#}", body)
                self.assertNotIn("{%", body)

    def test_the_login_page_no_longer_prints_its_own_source_comment(self):
        """The exact bug that was reported."""
        body = self.body(reverse("login"))
        self.assertNotIn("Django puts a bad username/password", body)

    def test_no_public_page_carries_an_old_brand_name(self):
        for url in self.public_pages():
            with self.subTest(url=url):
                body = self.body(url)
                self.assertNotIn("Dependable", body)
                self.assertNotIn("Fulfilled Lite", body)

    def test_the_product_is_named_on_every_public_page(self):
        for url in self.public_pages():
            with self.subTest(url=url):
                self.assertIn(PRODUCT_NAME, self.body(url))

    def test_the_landing_footer_is_the_product_and_not_a_codename(self):
        body = self.body(reverse("core:landing"))
        self.assertIn(f"&copy; ", body)
        self.assertIn(PRODUCT_NAME, body)
        self.assertNotIn("built on", body)


class BrandingIsAvailableSignedOutTests(TestCase):
    """The product name has to survive the anonymous context processor."""

    def test_the_context_processor_runs_for_anonymous_visitors(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.context["product_name"], PRODUCT_NAME)

    def test_the_page_title_uses_it(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, f"&middot; {PRODUCT_NAME}</title>")


class PasswordResetBrandingTests(TestCase):
    """The reset mail renders with no request, so it needs handing the name."""

    @classmethod
    def setUpTestData(cls):
        cls.school = School.all_objects.create(name="Bright Future Academy")
        cls.user = User.objects.create_user(
            "owner", email="owner@example.com", password="pw",
            role=Role.SCHOOL_OWNER, school=cls.school,
        )

    def test_the_subject_and_body_name_the_product(self):
        self.client.post(reverse("password_reset"), {"email": self.user.email})

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn(PRODUCT_NAME, message.subject)
        self.assertIn(PRODUCT_NAME, message.body)

    def test_it_never_ships_a_blank_where_the_name_should_be(self):
        """Without extra_email_context this renders 'Reset your  password'."""
        self.client.post(reverse("password_reset"), {"email": self.user.email})

        subject = mail.outbox[0].subject
        self.assertNotIn("  ", subject)
        self.assertNotIn("Dependable", subject)
        self.assertNotIn("Fulfilled Lite", subject)

    def test_the_from_address_is_branded(self):
        self.client.post(reverse("password_reset"), {"email": self.user.email})
        self.assertIn(PRODUCT_NAME, mail.outbox[0].from_email)


class ProductLogoTests(TestCase):
    """SCHOOLCORD's own mark: the asset, and every surface it has to reach."""

    SVG = pathlib.Path(settings.BASE_DIR) / "static" / "img" / "schoolcord-logo.svg"
    PNG = pathlib.Path(settings.BASE_DIR) / "static" / "img" / "schoolcord-logo.png"

    #: The brand tokens the mark is built from. Asserted so that a logo swapped
    #: in later cannot quietly drift off the palette the rest of the UI uses.
    BRAND_HEX = ("#EEF3FB", "#0F2A52", "#1B4680", "#2E63B0")

    def test_both_asset_formats_are_present(self):
        self.assertTrue(self.SVG.exists(), self.SVG)
        self.assertTrue(self.PNG.exists(), self.PNG)
        self.assertGreater(self.PNG.stat().st_size, 0)

    def test_the_svg_is_drawn_in_the_brand_palette(self):
        markup = self.SVG.read_text(encoding="utf-8")
        for hex_value in self.BRAND_HEX:
            with self.subTest(colour=hex_value):
                self.assertIn(hex_value, markup)

    def test_the_png_really_is_a_png(self):
        # The 8-byte PNG signature, written as hex so the escapes in it
        # cannot be mangled by whatever edits this file next.
        signature = bytes.fromhex("89504e470d0a1a0a")
        self.assertEqual(self.PNG.read_bytes()[:8], signature)

    def test_the_hand_drawn_placeholder_it_replaced_is_gone(self):
        response = self.client.get(reverse("login"))
        self.assertNotContains(response, "M12 4 3 8.4l9 4.4 9-4.4L12 4Z")

    def test_every_public_surface_shows_it(self):
        for url in (
            reverse("core:landing"),
            reverse("login"),
            reverse("core:signup"),
            reverse("password_reset"),
        ):
            with self.subTest(url=url):
                self.assertContains(
                    self.client.get(url), "img/schoolcord-logo.svg"
                )

    def test_the_favicon_offers_svg_first_with_a_png_fallback(self):
        body = self.client.get(reverse("login")).content.decode()
        self.assertIn('rel="icon"', body)
        self.assertIn('type="image/svg+xml"', body)
        self.assertIn('rel="alternate icon"', body)
        self.assertIn('rel="apple-touch-icon"', body)

    def test_the_png_fallback_is_wired_for_browsers_that_refuse_the_svg(self):
        body = self.client.get(reverse("login")).content.decode()
        self.assertIn("img/schoolcord-logo.png", body)


class ProductLogoOnSignedInSurfacesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = School.all_objects.create(name="Sunrise Academy")
        cls.branch = Branch.all_objects.create(school=cls.school, name="Main")
        cls.owner = User.objects.create_user(
            "logo.owner", password="pw", role=Role.SCHOOL_OWNER, school=cls.school
        )
        cls.platform = User.objects.create_user(
            "logo.platform", password="pw", role=Role.PLATFORM_OWNER
        )

    def test_onboarding_introduces_the_product(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("core:onboarding"))

        self.assertContains(response, "img/schoolcord-logo.svg")
        self.assertContains(response, f"Welcome to {PRODUCT_NAME}")

    def test_the_platform_dashboard_shows_it(self):
        self.client.force_login(self.platform)
        self.assertContains(
            self.client.get(reverse("core:platform_overview")),
            "img/schoolcord-logo.svg",
        )

    def test_the_sidebar_carries_it_on_every_signed_in_screen(self):
        self.client.force_login(self.owner)
        self.assertContains(
            self.client.get(reverse("core:school_dashboard")),
            "img/schoolcord-logo.svg",
        )


class PasswordResetEmailLogoTests(TestCase):
    """The mail keeps its plain-text body and gains a branded HTML half."""

    @classmethod
    def setUpTestData(cls):
        cls.school = School.all_objects.create(name="Sunrise Academy")
        cls.user = User.objects.create_user(
            "reset.owner", email="reset@example.com", password="pw",
            role=Role.SCHOOL_OWNER, school=cls.school,
        )

    def send(self):
        self.client.post(reverse("password_reset"), {"email": self.user.email})
        return mail.outbox[0]

    def test_it_is_multipart_so_a_text_only_client_still_gets_everything(self):
        message = self.send()

        self.assertEqual(len(message.alternatives), 1)
        self.assertEqual(message.alternatives[0][1], "text/html")
        # The plain-text half must still stand on its own.
        self.assertIn(PRODUCT_NAME, message.body)
        self.assertIn("/accounts/reset/", message.body)

    def test_the_html_half_carries_the_logo_at_an_absolute_url(self):
        html = self.send().alternatives[0][0]

        match = re.search(r'<img src="([^"]+)"', html)
        self.assertIsNotNone(match, "no logo in the HTML email")
        source = match.group(1)
        # A mail client has no site to be relative to.
        self.assertTrue(source.startswith("http"), source)
        self.assertIn("img/schoolcord-logo.png", source)

    def test_the_logo_is_a_png_because_email_clients_do_not_render_svg(self):
        html = self.send().alternatives[0][0]
        self.assertNotIn("schoolcord-logo.svg", html)

    def test_a_client_that_blocks_images_still_sees_the_product_name(self):
        html = self.send().alternatives[0][0]
        self.assertIn(f'alt="{PRODUCT_NAME}"', html)

    def test_the_html_renders_no_leftover_template_syntax(self):
        html = self.send().alternatives[0][0]
        self.assertNotIn("{%", html)
        self.assertNotIn("{{", html)


class SchoolLogoTests(TestCase):
    """Named away from "Bright Future Academy" on purpose: that string is the
    school-name field's own placeholder, so a test asserting it is absent from
    another tenant's page would be testing the placeholder, not isolation."""

    @classmethod
    def setUpTestData(cls):
        cls.school = School.all_objects.create(name="Sunrise Academy")
        cls.branch = Branch.all_objects.create(school=cls.school, name="Main")
        cls.owner = User.objects.create_user(
            "bf.owner", password="pw", role=Role.SCHOOL_OWNER, school=cls.school
        )
        cls.other = School.all_objects.create(name="Demo School")
        cls.other_owner = User.objects.create_user(
            "demo.owner", password="pw", role=Role.SCHOOL_OWNER, school=cls.other
        )

    def upload(self, name="crest.png", content=PNG_1PX):
        return SimpleUploadedFile(name, content, content_type="image/png")

    def test_a_school_starts_with_no_logo_and_falls_back_to_its_initial(self):
        self.assertFalse(self.school.has_logo)
        self.assertEqual(self.school.initial, "S")

    def test_a_nameless_school_still_renders_something_deliberate(self):
        self.assertEqual(School(name="  ").initial, "?")

    def test_the_settings_screen_uploads_one(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("core:school_settings"),
            {
                "name": "Sunrise Academy",
                "contact_email": "office@example.com",
                "contact_phone": "",
                "logo": self.upload(),
            },
        )

        self.assertRedirects(response, reverse("core:school_settings"))
        self.school.refresh_from_db()
        self.assertTrue(self.school.has_logo)
        self.school.logo.delete(save=True)

    def test_it_refuses_something_that_is_not_an_image(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("core:school_settings"),
            {
                "name": "Sunrise Academy",
                "contact_email": "office@example.com",
                "contact_phone": "",
                "logo": SimpleUploadedFile("payload.exe", b"MZ"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.school.refresh_from_db()
        self.assertFalse(self.school.has_logo)

    def test_the_fallback_square_renders_when_there_is_no_logo(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("core:school_settings"))

        self.assertEqual(response.status_code, 200)
        # The initial, not a broken image.
        self.assertContains(response, ">S</span>", html=False)
        # Specifically no *uploaded* logo. Matching on "logo.png" would now
        # catch the product's own favicon, which is on every page by design.
        self.assertNotContains(response, f"{settings.MEDIA_URL}logos/")

    def test_a_school_only_ever_edits_its_own_profile(self):
        """There is no id in the URL; the object comes from the caller."""
        self.client.force_login(self.other_owner)
        response = self.client.get(reverse("core:school_settings"))

        self.assertEqual(response.context["object"], self.other)
        self.assertContains(response, "Demo School")
        self.assertNotContains(response, "Sunrise Academy")

    def test_platform_staff_have_no_school_of_their_own_to_configure(self):
        platform = User.objects.create_user(
            "platform", password="pw", role=Role.PLATFORM_OWNER
        )
        self.client.force_login(platform)
        self.assertEqual(
            self.client.get(reverse("core:school_settings")).status_code, 404
        )

    def test_a_bursar_cannot_change_the_schools_identity(self):
        bursar = User.objects.create_user(
            "bf.bursar", password="pw", role=Role.BURSAR,
            school=self.school, branch=self.branch,
        )
        self.client.force_login(bursar)
        self.assertEqual(
            self.client.get(reverse("core:school_settings")).status_code, 403
        )


class NavigationTests(TestCase):
    def test_settings_points_at_a_screen_that_exists_now(self):
        from apps.core.navigation import nav_for

        sections = {s.label: s.items for s in nav_for(Role.SCHOOL_OWNER, "/")}
        item = next(i for i in sections["Settings"] if i.label == "Settings")
        self.assertTrue(item.available)
        self.assertEqual(item.href, reverse("core:school_settings"))


class DemoDataTests(TestCase):
    """The seeded roster must not be able to reach a real person."""

    def test_every_demo_parent_number_is_on_the_unroutable_block(self):
        from apps.students.roster import ROSTERS

        numbers = {
            student.parent_phone
            for roster in ROSTERS
            for student in roster.students
        }
        self.assertTrue(numbers)
        for number in sorted(numbers):
            with self.subTest(number=number):
                # 0800 is a Nigerian toll-free service range, not a handset
                # range: valid to the validator, impossible to deliver to.
                self.assertTrue(number.startswith("0800"), number)

    def test_every_demo_parent_email_is_on_a_reserved_domain(self):
        from apps.students.roster import ROSTERS

        for roster in ROSTERS:
            for student in roster.students:
                if student.parent_email:
                    with self.subTest(email=student.parent_email):
                        self.assertTrue(
                            student.parent_email.endswith("@example.com"),
                            student.parent_email,
                        )

    def test_the_local_scaffold_does_not_look_like_a_customer(self):
        from apps.core.management.commands.bootstrap_tenant import Command

        parser = Command().create_parser("manage.py", "bootstrap_tenant")
        defaults = {a.dest: a.default for a in parser._actions}
        self.assertEqual(defaults["name"], "Demo School")
        self.assertNotIn("dependable", str(defaults["password"]).lower())
