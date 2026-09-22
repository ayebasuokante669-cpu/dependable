"""Tests for the toast notification system.

The rule the whole thing rests on: **a view flashes an ordinary Django message
and never learns that toasts exist.** Every test here therefore checks the
message, not the animation -- what a view is responsible for is saying what
happened, and the presentation layer is free to change without touching a
single view.

Three things must hold:

* an action that succeeds, fails, or is refused produces a message;
* that message reaches the page, *including on the signed-out screens*, which
  before this rendered ``messages`` nowhere at all and silently swallowed
  anything flashed on the way to them;
* the rendered toast never signals its category by colour alone.
"""

from __future__ import annotations

import re
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.core.roles import Role
from apps.schools.models import Branch, School

User = get_user_model()


def toast_levels(response) -> list[str]:
    """The category of every toast rendered into a response."""
    html = response.content.decode()
    return re.findall(r'data-toast-level="(\w+)"', html)


def toast_text(response) -> str:
    """Every toast's message, joined -- enough to assert what was said."""
    html = response.content.decode()
    bodies = re.findall(
        r'<p class="toast-message">\s*<span class="sr-only">[^<]*</span>([^<]*)',
        html,
    )
    return " ".join(body.strip() for body in bodies)


def flashed(response) -> list[tuple[str, str]]:
    """(level, message) straight off the request, before rendering."""
    return [
        (message.tags, str(message))
        for message in response.context["messages"]
    ]


class FeedbackTestCase(TestCase):
    password = "scaffold-password"

    @classmethod
    def setUpTestData(cls):
        call_command(
            "bootstrap_tenant",
            "--name", "Fulfilled Academy",
            "--password", cls.password,
            stdout=StringIO(),
        )
        cls.school = School.all_objects.get(name="Fulfilled Academy")
        cls.branch = Branch.all_objects.get(school=cls.school)
        cls.owner = User.objects.get(username="fulfilled-academy.owner")
        cls.bursar = User.objects.filter(role=Role.BURSAR).first()


class SigningInAndOutTests(FeedbackTestCase):
    """The two actions that previously confirmed themselves with nothing."""

    def test_a_successful_sign_in_says_who_you_are(self):
        response = self.client.post(
            reverse("login"),
            {"username": self.owner.username, "password": self.password},
            follow=True,
        )
        self.assertEqual(toast_levels(response), ["success"])
        self.assertIn("Signed in as", toast_text(response))

    def test_a_failed_sign_in_says_so(self):
        response = self.client.post(
            reverse("login"),
            {"username": self.owner.username, "password": "not-it"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(toast_levels(response), ["error"])
        self.assertIn("Sign-in failed", toast_text(response))

    def test_signing_out_confirms_it_survived_the_session_flush(self):
        """logout() empties the session, which is where messages live.

        The confirmation has to be written *after* that, or it is flushed with
        everything else and the user lands on a landing page identical to the
        one they would see having never signed in.
        """
        self.client.force_login(self.owner)
        response = self.client.post(reverse("logout"), follow=True)
        self.assertEqual(toast_levels(response), ["success"])
        self.assertIn("Signed out", toast_text(response))

    def test_signing_up_confirms_the_school_exists(self):
        response = self.client.post(
            reverse("core:signup"),
            {
                "school_name": "Bright Star Academy",
                "full_name": "Ngozi Okonkwo",
                "email": "ngozi@brightstar.example",
                "password": "correct-horse-battery",
                "password_confirm": "correct-horse-battery",
            },
            follow=True,
        )
        self.assertEqual(toast_levels(response), ["success"])
        self.assertIn("Bright Star Academy", toast_text(response))


class SignedOutPagesCarryToastsTests(FeedbackTestCase):
    """The regression this system fixes.

    ``messages`` used to be rendered only inside the signed-in shell, so any
    message flashed on the way to a signed-out screen was consumed by the
    template engine and shown to nobody. The region now lives at the end of
    <body> in base.html, outside that branch.
    """

    def test_the_sign_in_page_can_render_a_message(self):
        response = self.client.post(
            reverse("login"),
            {"username": "nobody", "password": "nothing"},
        )
        self.assertIn("toast-region", response.content.decode())

    def test_a_page_with_nothing_to_say_renders_no_region(self):
        """An empty live region on every page is clutter in the DOM."""
        response = self.client.get(reverse("core:landing"))
        self.assertNotIn("toast-region", response.content.decode())


class RefusalTests(FeedbackTestCase):
    """Authorisation feedback, which is the point of the handler403 override."""

    denied_url = "academics:class_bulk_create"

    def test_a_refusal_is_still_a_refusal(self):
        """The status code is the security contract; it does not move."""
        self.client.force_login(self.bursar)
        response = self.client.get(reverse(self.denied_url))
        self.assertEqual(response.status_code, 403)

    def test_a_refusal_says_why(self):
        self.client.force_login(self.bursar)
        response = self.client.get(reverse(self.denied_url))
        self.assertEqual(toast_levels(response), ["error"])
        self.assertIn("does not allow", toast_text(response))

    def test_a_refusal_never_asks_for_credentials_again(self):
        """A signed-in user who lacks a capability is already who they are.

        Asking them to authenticate a second time answers a question nobody
        asked and hides the real one. The refusal carries no password field
        and no link to one.
        """
        self.client.force_login(self.bursar)
        html = self.client.get(reverse(self.denied_url)).content.decode()
        self.assertNotIn('type="password"', html)
        self.assertNotIn(reverse("login"), html)


class AuthPanelSwitchTests(FeedbackTestCase):
    """Sign-in and sign-up render both panels so switching costs no round trip.

    The hazard that creates: "is the form on the page?" stops being the same
    question as "is the form *showing*?". Both forms are always present, so an
    assertion that merely finds one passes on the wrong page -- which is
    exactly how /signup/ came to render the sign-in panel.
    """

    def panels(self, url):
        """(visible, hidden) panel names for a rendered auth page."""
        html = self.client.get(url).content.decode()
        visible, hidden = set(), set()
        for kind in ("form", "visual"):
            for side in ("login", "signup"):
                match = re.search(
                    rf'<div data-auth-{kind}-for="{side}"( hidden)?>', html
                )
                self.assertIsNotNone(match, f"{kind}/{side} missing from {url}")
                (hidden if match.group(1) else visible).add(f"{kind}/{side}")
        return visible, hidden

    def test_the_sign_in_url_shows_the_sign_in_panel(self):
        visible, hidden = self.panels(reverse("login"))
        self.assertEqual(visible, {"form/login", "visual/login"})
        self.assertEqual(hidden, {"form/signup", "visual/signup"})

    def test_the_sign_up_url_shows_the_sign_up_panel(self):
        visible, hidden = self.panels(reverse("core:signup"))
        self.assertEqual(visible, {"form/signup", "visual/signup"})
        self.assertEqual(hidden, {"form/login", "visual/login"})

    def test_both_forms_are_present_on_both_urls(self):
        """That presence is the whole point -- it is what makes the switch
        instant. It is also what makes the two tests above necessary."""
        for url in (reverse("login"), reverse("core:signup")):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn('name="username"', html)
                self.assertIn('name="school_name"', html)

    def test_each_form_posts_to_its_own_view(self):
        """A sign-in submitted while the URL says /signup/ must still reach the
        login view, so the action cannot be left to default to the page."""
        html = self.client.get(reverse("core:signup")).content.decode()
        self.assertIn(f'action="{reverse("login")}"', html)
        self.assertIn(f'action="{reverse("core:signup")}"', html)

    def test_the_switch_links_stay_real_urls(self):
        """js/ui.js intercepts them; with the script blocked they must still
        navigate."""
        html = self.client.get(reverse("login")).content.decode()
        self.assertIn(f'href="{reverse("core:signup")}"', html)
        self.assertIn('data-auth-switch="signup"', html)


class RedirectsThatExplainThemselvesTests(FeedbackTestCase):
    """A redirect the user did not ask for has to say why it happened."""

    def test_a_temporary_password_explains_the_detour(self):
        """Pressing "Students" and landing on Account settings is confusing
        unless something says so.

        The settings page already carries a persistent notice about the *state*
        -- correct, because this is something the user must act on -- but
        nothing there explained the detour itself.
        """
        self.owner.must_change_password = True
        self.owner.save(update_fields=["must_change_password"])
        self.client.force_login(self.owner)

        response = self.client.get(reverse("students:student_list"), follow=True)
        self.assertRedirects(response, reverse("accounts:settings"))
        self.assertIn("warning", toast_levels(response))
        self.assertIn("Choose your own password", toast_text(response))


class ToastRenderingTests(FeedbackTestCase):
    """What actually reaches the browser."""

    def sign_in_response(self):
        return self.client.post(
            reverse("login"),
            {"username": self.owner.username, "password": self.password},
            follow=True,
        )

    def test_the_category_is_a_class_and_a_word_not_only_a_colour(self):
        """WCAG 1.4.1: colour is never the only carrier of meaning."""
        html = self.sign_in_response().content.decode()
        self.assertIn("toast-success", html)
        self.assertIn('<span class="sr-only">Success: </span>', html)

    def test_a_toast_ships_open_so_it_survives_javascript_being_blocked(self):
        """The markup is readable as rendered; the script animates it after.

        If this ever shipped closed, a blocked script would leave the user
        looking at a circle with no message in it.
        """
        html = self.sign_in_response().content.decode()
        self.assertIn("toast toast-success is-in is-open", html)

    def test_the_region_is_a_polite_live_region(self):
        html = self.sign_in_response().content.decode()
        self.assertIn('aria-live="polite"', html)

    def test_success_carries_no_dismiss_button(self):
        """Nothing to act on, so nothing to press. It simply goes."""
        html = self.sign_in_response().content.decode()
        self.assertNotIn("data-toast-close", html)

    def test_a_problem_can_be_dismissed_by_hand(self):
        """An error holds longer and offers a way out, because the reader may
        want to keep it on screen while they fix what it names."""
        response = self.client.post(
            reverse("login"),
            {"username": self.owner.username, "password": "not-it"},
        )
        self.assertIn("data-toast-close", response.content.decode())


class ExistingActionsStillFlashTests(FeedbackTestCase):
    """A spot check that the ordinary write paths reach the toast layer.

    These views were flashing messages long before toasts existed and were not
    touched by this change -- which is the point. If the presentation layer is
    wired correctly, they light up for free.
    """

    def test_creating_a_class_confirms_it(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("academics:class_bulk_create"),
            {
                "branch": str(self.branch.pk),
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-name": "Reception Blue",
                "form-0-level": "",
                "form-0-year_in_level": "",
                "form-0-stream": "",
            },
            follow=True,
        )
        self.assertEqual(toast_levels(response), ["success"])
        self.assertIn("added", toast_text(response))

    def test_an_owner_updating_their_school_confirms_it(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("core:school_settings"),
            {
                "name": "Fulfilled Academy",
                "contact_email": "office@fulfilled.example",
                "contact_phone": "08030000000",
            },
            follow=True,
        )
        self.assertTrue(flashed(response), "the save said nothing at all")
