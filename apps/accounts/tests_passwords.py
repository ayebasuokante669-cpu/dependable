"""The password policy, the breach check, and the generator.

Every test that involves Have I Been Pwned stubs the API client. The suite must
not depend on a third-party service being reachable, and it must not send
hash prefixes of test passwords to it either. ``PWNED_PASSWORDS_ENABLED``
defaults to off under the test runner, so the tests that care about the breach
check turn it back on explicitly -- which also documents which behaviour each
one is actually exercising.
"""

from __future__ import annotations

import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from pwned_passwords_django.api import PwnedPasswords
from pwned_passwords_django.exceptions import ErrorCode, PwnedPasswordsError

from apps.accounts.passwords import (
    GROUP_SIZE,
    GROUPS,
    MAX_ATTEMPTS,
    UNAMBIGUOUS_ALPHABET,
    generate_password,
)

User = get_user_model()

#: Long, unique, and not in any breach corpus -- the kind of password the
#: policy is meant to wave through.
GOOD_PASSWORD = "ravine-tapestry-quorum-58"


def breach_error() -> PwnedPasswordsError:
    """The exception the library raises when the API is unreachable."""
    return PwnedPasswordsError(
        message="Pwned Passwords API timed out.",
        code=ErrorCode.API_TIMEOUT,
        params={"timeout_threshold": 1.5},
    )


def stub_hits(count: int):
    """Patch the API client to report ``count`` breach hits, with no network."""
    return mock.patch.object(PwnedPasswords, "check_password", return_value=count)


def stub_unreachable():
    """Patch the API client to fail the way a real outage does."""
    return mock.patch.object(
        PwnedPasswords, "check_password", side_effect=breach_error()
    )


class PolicyRejectionTests(TestCase):
    """Each way a password can fail, one at a time."""

    def assert_rejected(self, password, expected_fragment, user=None):
        with self.assertRaises(ValidationError) as caught:
            validate_password(password, user=user)
        joined = " ".join(caught.exception.messages).lower()
        self.assertIn(expected_fragment, joined, f"got: {joined}")

    def test_rejects_a_password_under_eight_characters(self):
        # Seven characters, otherwise unremarkable -- so length is the only
        # thing that can be failing here.
        self.assert_rejected("gK7wq2z", "at least 8 characters")

    def test_rejects_an_entirely_numeric_password(self):
        self.assert_rejected("39481027465", "entirely numeric")

    def test_rejects_a_password_from_djangos_common_list(self):
        self.assert_rejected("football", "too common")

    def test_rejects_a_password_that_resembles_the_users_own_details(self):
        user = User(username="ada.obi", email="ada.obi@school.test")
        self.assert_rejected("ada.obi@school.test", "too similar", user=user)

    @override_settings(PWNED_PASSWORDS_ENABLED=True)
    def test_rejects_a_breached_password(self):
        # Passes every local validator: long, mixed, not in Django's list. The
        # only thing wrong with it is that the breach corpus has seen it, which
        # is the whole reason the API call exists.
        with stub_hits(52_372_427):
            self.assert_rejected("Tr0ub4dor&3xkcd", "known data breach")

    @override_settings(PWNED_PASSWORDS_ENABLED=True)
    def test_a_single_breach_hit_is_enough(self):
        with stub_hits(1):
            self.assert_rejected(GOOD_PASSWORD, "known data breach")


class PolicyAcceptanceTests(TestCase):
    @override_settings(PWNED_PASSWORDS_ENABLED=True)
    def test_accepts_a_long_unique_password(self):
        with stub_hits(0) as checked:
            validate_password(GOOD_PASSWORD)  # must not raise
        self.assertEqual(checked.call_count, 1)

    @override_settings(PWNED_PASSWORDS_ENABLED=True)
    def test_accepts_an_eight_character_password_at_the_boundary(self):
        # NIST's floor is a floor, not a suggestion of what is comfortable:
        # exactly 8 has to pass, or the stated policy is not the real one.
        with stub_hits(0):
            validate_password("qN4vTm7z")

    @override_settings(PWNED_PASSWORDS_ENABLED=True)
    def test_accepts_a_long_passphrase_with_spaces_and_no_symbols(self):
        # No uppercase, no digits, no punctuation. NIST explicitly wants this
        # to pass -- composition rules are what produce P@ssw0rd1.
        with stub_hits(0):
            validate_password("several quiet herons by the river")


class BreachCheckFailureTests(TestCase):
    """What happens when Have I Been Pwned cannot be reached.

    The requirement is to fail open: an outage in a third-party service must
    not stop somebody setting a password. The library's own behaviour is to
    fall back to Django's ``CommonPasswordValidator`` -- which is already
    listed separately in ``AUTH_PASSWORD_VALIDATORS``, so the fallback can only
    reject what the policy rejects anyway. These tests pin both halves of that.
    """

    @override_settings(PWNED_PASSWORDS_ENABLED=True)
    def test_api_failure_lets_a_good_password_through(self):
        with stub_unreachable():
            validate_password(GOOD_PASSWORD)  # must not raise

    @override_settings(PWNED_PASSWORDS_ENABLED=True)
    def test_api_failure_is_logged_rather_than_raised(self):
        with stub_unreachable():
            with self.assertLogs("pwned_passwords_django.validators", "ERROR") as logs:
                validate_password(GOOD_PASSWORD)
        self.assertIn("Pwned Passwords", " ".join(logs.output))

    @override_settings(PWNED_PASSWORDS_ENABLED=True)
    def test_api_failure_still_rejects_an_obviously_weak_password(self):
        # Failing open is not failing blind: the local validators are unchanged
        # by the outage, so a common password is still refused.
        with stub_unreachable():
            with self.assertRaises(ValidationError):
                validate_password("football")

    @override_settings(PWNED_PASSWORDS_ENABLED=False)
    def test_disabled_check_makes_no_network_call(self):
        with mock.patch.object(PwnedPasswords, "check_password") as checked:
            validate_password(GOOD_PASSWORD)
        checked.assert_not_called()


class GeneratorTests(TestCase):
    """The generator's contract: whatever it returns, the policy accepts."""

    #: Enough runs that a shape which is only usually compliant would show up.
    RUNS = 200

    @override_settings(PWNED_PASSWORDS_ENABLED=True)
    def test_every_generated_password_satisfies_the_policy(self):
        with stub_hits(0):
            for _ in range(self.RUNS):
                password = generate_password()
                try:
                    validate_password(password)
                except ValidationError as error:
                    self.fail(f"{password!r} failed the policy: {error.messages}")

    def test_generated_passwords_have_the_documented_shape(self):
        pattern = re.compile(
            r"^[%s]{%d}(?:-[%s]{%d}){%d}$"
            % (
                re.escape(UNAMBIGUOUS_ALPHABET),
                GROUP_SIZE,
                re.escape(UNAMBIGUOUS_ALPHABET),
                GROUP_SIZE,
                GROUPS - 1,
            )
        )
        for _ in range(self.RUNS):
            self.assertRegex(generate_password(), pattern)

    def test_generated_passwords_exceed_the_minimum_length(self):
        for _ in range(self.RUNS):
            self.assertGreaterEqual(len(generate_password()), 8)

    def test_alphabet_excludes_lookalike_characters(self):
        for char in "IlO01":
            self.assertNotIn(char, UNAMBIGUOUS_ALPHABET)

    def test_generated_passwords_are_not_repeated(self):
        # ~93 bits of entropy: a collision in 200 draws would mean the source
        # is not random, which is the failure mode worth catching here.
        seen = {generate_password() for _ in range(self.RUNS)}
        self.assertEqual(len(seen), self.RUNS)

    def test_gives_up_rather_than_looping_when_the_policy_cannot_be_met(self):
        # A policy that rejects everything. The generator must raise, not spin.
        with mock.patch(
            "apps.accounts.passwords.validate_password",
            side_effect=ValidationError("no"),
        ) as validator:
            with self.assertRaises(RuntimeError):
                generate_password()
        self.assertEqual(validator.call_count, MAX_ATTEMPTS)


class GeneratorEndpointTests(TestCase):
    def setUp(self):
        # The throttle counts in the cache, which is process-wide and would
        # otherwise leak between tests.
        cache.clear()
        self.url = reverse("accounts:generate_password")

    def test_post_returns_a_policy_compliant_password(self):
        with override_settings(PWNED_PASSWORDS_ENABLED=True):
            with stub_hits(0):
                response = self.client.post(self.url)
                self.assertEqual(response.status_code, 200)
                password = response.json()["password"]
                validate_password(password)  # must not raise

    def test_get_is_not_allowed(self):
        # POST-only so it is CSRF-checked and never cached in front of us -- a
        # cached response would hand the same password to everyone.
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_successive_calls_return_different_passwords(self):
        first = self.client.post(self.url).json()["password"]
        second = self.client.post(self.url).json()["password"]
        self.assertNotEqual(first, second)

    def test_throttles_repeated_requests(self):
        from apps.accounts.views import RATE_LIMIT

        for _ in range(RATE_LIMIT):
            self.assertEqual(self.client.post(self.url).status_code, 200)

        throttled = self.client.post(self.url)
        self.assertEqual(throttled.status_code, 429)
        self.assertIn("error", throttled.json())

    def test_throttle_is_per_client(self):
        from apps.accounts.views import RATE_LIMIT

        for _ in range(RATE_LIMIT + 1):
            self.client.post(self.url, REMOTE_ADDR="10.0.0.1")

        other = self.client.post(self.url, REMOTE_ADDR="10.0.0.2")
        self.assertEqual(other.status_code, 200)

    def test_generator_failure_reports_an_error_rather_than_a_500(self):
        with mock.patch(
            "apps.accounts.views.generate_password",
            side_effect=RuntimeError("policy unsatisfiable"),
        ):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertIn("error", response.json())


class SignupFormPolicyTests(TestCase):
    """The policy reaches the form a user actually meets.

    Configuring validators in settings is only worth anything if the forms run
    them, so this exercises the real signup POST rather than
    ``validate_password`` directly.
    """

    def setUp(self):
        self.url = reverse("core:signup")
        self.base = {
            "school_name": "Riverbank Academy",
            "full_name": "Ada Obi",
            "email": "ada@riverbank.test",
        }

    def post(self, password):
        return Client().post(
            self.url,
            {**self.base, "password": password, "password_confirm": password},
        )

    @override_settings(PWNED_PASSWORDS_ENABLED=True)
    def test_signup_refuses_a_breached_password(self):
        with stub_hits(4_173):
            response = self.post("correcthorsebatterystaple")
        self.assertEqual(response.status_code, 200)  # redisplayed, not created
        self.assertIn("password", response.context["form"].errors)
        self.assertFalse(User.objects.filter(email="ada@riverbank.test").exists())

    def test_signup_refuses_a_short_password(self):
        response = self.post("gK7wq2z")
        self.assertIn("password", response.context["form"].errors)
        self.assertFalse(User.objects.filter(email="ada@riverbank.test").exists())

    @override_settings(PWNED_PASSWORDS_ENABLED=True)
    def test_signup_accepts_a_strong_password(self):
        with stub_hits(0):
            response = self.post(GOOD_PASSWORD)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(email="ada@riverbank.test").exists())

    @override_settings(PWNED_PASSWORDS_ENABLED=True)
    def test_signup_survives_the_breach_api_being_down(self):
        # The point of failing open: an outage at HIBP must not stop a school
        # signing up.
        with stub_unreachable():
            response = self.post(GOOD_PASSWORD)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(email="ada@riverbank.test").exists())
