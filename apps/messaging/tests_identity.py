"""Tests for per-tenant messaging identity.

The three rules that matter, because each is a way for a parent to receive a
message that lies about who sent it:

* a school's message goes out under *its own* registered Sender ID;
* a school with no approved Sender ID sends nothing, and is told why;
* one tenant can never send under, see, or claim another's identity.

Everything here runs on the console provider, which logs the Sender ID it would
have used -- so per-tenant identity is verifiable with no credentials and no
money spent, which is the whole point of that provider existing.
"""

from __future__ import annotations

import io
import json
import urllib.error
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.utils import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.academics.models import Class, Level
from apps.core.permissions import Capability, has_capability
from apps.core.roles import Role
from apps.core.tenancy import scope_to
from apps.schools.models import Branch, School
from apps.students.models import Sex, Student

from . import audiences, dispatch, identity as identity_module
from .forms import MessagingIdentityForm
from .identity import SenderIdentityUnavailable
from .models import (
    AudienceType,
    Message,
    MessageStatus,
    SchoolMessagingConfig,
    SenderIdStatus,
)
from .providers import (
    BulkSMSNigeriaProvider,
    Channel,
    ConsoleProvider,
    DeliveryStatus,
    MessagePurpose,
    ProviderKey,
    ProviderNotConfigured,
    SenderIdentity,
    TermiiProvider,
    get_provider,
)
from .validators import normalise_sender_id, validate_sender_id

User = get_user_model()


class IdentityTestCase(TestCase):
    """Two schools, each with a branch, a class and a parent to message.

    Dap Group is the worked example from the brief: it sends as "Dapgroup"
    through Termii on the platform's master account. Beta College
    exists so every isolation assertion has a real second tenant to fail
    against rather than an empty one.
    """

    @classmethod
    def setUpTestData(cls):
        cls.dap = School.all_objects.create(name="Dap Group of Schools")
        cls.dap_main = Branch.all_objects.create(school=cls.dap, name="Main")
        cls.dap_annex = Branch.all_objects.create(school=cls.dap, name="Annex")

        cls.beta = School.all_objects.create(name="Beta College")
        cls.beta_main = Branch.all_objects.create(school=cls.beta, name="Beta Main")

        cls.platform = User.objects.create_user(
            "platform.owner", password="pw", role=Role.PLATFORM_OWNER
        )
        cls.dap_owner = User.objects.create_user(
            "dap.owner", password="pw", role=Role.SCHOOL_OWNER, school=cls.dap
        )
        cls.dap_principal = User.objects.create_user(
            "dap.principal", password="pw", role=Role.PRINCIPAL,
            school=cls.dap, branch=cls.dap_main,
        )
        cls.dap_bursar = User.objects.create_user(
            "dap.bursar", password="pw", role=Role.BURSAR,
            school=cls.dap, branch=cls.dap_main,
        )
        cls.beta_bursar = User.objects.create_user(
            "beta.bursar", password="pw", role=Role.BURSAR,
            school=cls.beta, branch=cls.beta_main,
        )

        cls.dap_class = Class.all_objects.create(
            branch=cls.dap_main, name="JSS 1", stream="A",
            level=Level.JUNIOR_SECONDARY, year_in_level=1,
        )
        cls.beta_class = Class.all_objects.create(
            branch=cls.beta_main, name="JSS 1", level=Level.JUNIOR_SECONDARY,
            year_in_level=1,
        )
        cls.dap_student = cls.make_student("D/001", "Ada", cls.dap_class)
        cls.beta_student = cls.make_student("B/001", "Bola", cls.beta_class)

    @classmethod
    def make_student(cls, number, first, klass):
        return Student.all_objects.create(
            branch=klass.branch,
            school_class=klass,
            admission_number=number,
            first_name=first,
            last_name="Test",
            sex=Sex.FEMALE,
            parent_name=f"Parent of {first}",
            parent_phone="08031234567",
        )

    @classmethod
    def configure(cls, school, sender_id, *, branch=None,
                  status=SenderIdStatus.APPROVED, **extra):
        return SchoolMessagingConfig.all_objects.create(
            school=school,
            branch=branch,
            sender_id=sender_id,
            provider=extra.pop("provider", ProviderKey.TERMII),
            status=status,
            **extra,
        )

    def as_user(self, user):
        return scope_to(
            school_id=user.school_id, branch_id=user.branch_id, role=user.role
        )

    def send_from(self, branch, body="Fees are due.", provider=None):
        """Message that branch's parents, however dispatch decides to do it."""
        with self.as_user(self.user_for(branch)):
            audience = audiences.resolve(AudienceType.ALL, branch=branch)
            return dispatch.send(
                body=body,
                channel=Channel.SMS,
                audience=audience,
                branch=branch,
                sender=self.user_for(branch),
                provider=provider,
            )

    def user_for(self, branch):
        return self.dap_bursar if branch.school_id == self.dap.pk else self.beta_bursar


# ===========================================================================
# The Sender ID itself
# ===========================================================================


class SenderIdValidationTests(TestCase):
    def test_a_normal_school_name_is_accepted(self):
        for value in ("Dapgroup", "Fulfilled", "St Marys", "GHS-Ikeja", "A1School"):
            with self.subTest(sender_id=value):
                validate_sender_id(value)

    def test_more_than_eleven_characters_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            validate_sender_id("DapGroupOfSchools")
        self.assertEqual(caught.exception.code, "sender_id_too_long")

    def test_punctuation_a_gateway_would_mangle_is_refused(self):
        for value in ("Dap&Group", "Dap_Group", "Dap.Group", "Dap!"):
            with self.subTest(sender_id=value):
                with self.assertRaises(ValidationError):
                    validate_sender_id(value)

    def test_an_all_numeric_sender_is_refused(self):
        """That is a short code, which is a different registration entirely."""
        with self.assertRaises(ValidationError) as caught:
            validate_sender_id("2348031234")
        self.assertEqual(caught.exception.code, "sender_id_numeric")

    def test_whitespace_is_collapsed_but_casing_is_the_schools_own(self):
        self.assertEqual(normalise_sender_id("  Dap   Group "), "Dap Group")
        self.assertEqual(normalise_sender_id("DAPGROUP"), "DAPGROUP")

    def test_exactly_eleven_characters_is_allowed(self):
        validate_sender_id("Dapgroup123")


class ConfigModelTests(IdentityTestCase):
    def test_a_pending_sender_id_is_not_usable(self):
        config = self.configure(
            self.dap, "Dapgroup", status=SenderIdStatus.PENDING
        )
        self.assertFalse(config.is_usable)
        self.assertFalse(config.is_approved)

    def test_approving_stamps_who_and_when(self):
        config = self.configure(
            self.dap, "Dapgroup", status=SenderIdStatus.PENDING
        )
        config.approve(by=self.platform)

        config.refresh_from_db()
        self.assertTrue(config.is_usable)
        self.assertEqual(config.approved_by, self.platform)
        self.assertIsNotNone(config.approved_at)

    def test_a_school_cannot_have_two_school_wide_identities(self):
        """SQL treats NULLs as distinct, so this needs its own constraint."""
        self.configure(self.dap, "Dapgroup")
        with self.assertRaises(IntegrityError):
            self.configure(self.dap, "Dapgroup2")

    def test_a_branch_may_have_its_own_alongside_the_default(self):
        self.configure(self.dap, "Dapgroup")
        annex = self.configure(self.dap, "DapAnnex", branch=self.dap_annex)
        self.assertEqual(annex.scope_label, "Annex")

    def test_the_sender_id_is_normalised_on_save(self):
        config = self.configure(self.dap, "  Dap  Group ")
        self.assertEqual(config.sender_id, "Dap Group")

    def test_blank_credentials_mean_the_platform_account(self):
        config = self.configure(self.dap, "Dapgroup")
        self.assertFalse(config.uses_own_credentials)
        self.assertTrue(self.configure(
            self.beta, "Betacol", api_key="school-key"
        ).uses_own_credentials)


# ===========================================================================
# Resolution
# ===========================================================================


class ResolutionTests(IdentityTestCase):
    def test_a_school_resolves_to_its_own_identity(self):
        self.configure(self.dap, "Dapgroup")

        identity = identity_module.resolve_for_branch(self.dap_main)

        self.assertEqual(identity.sender_id, "Dapgroup")
        self.assertEqual(identity.provider_key, ProviderKey.TERMII)
        self.assertEqual(identity.school_name, "Dap Group of Schools")
        self.assertFalse(identity.uses_own_credentials)

    def test_two_schools_resolve_to_two_different_identities(self):
        self.configure(self.dap, "Dapgroup")
        self.configure(self.beta, "Betacol", provider=ProviderKey.TERMII)

        self.assertEqual(
            identity_module.resolve_for_branch(self.dap_main).sender_id, "Dapgroup"
        )
        beta = identity_module.resolve_for_branch(self.beta_main)
        self.assertEqual(beta.sender_id, "Betacol")
        self.assertEqual(beta.provider_key, ProviderKey.TERMII)

    def test_a_branch_identity_beats_the_school_wide_one(self):
        self.configure(self.dap, "Dapgroup")
        self.configure(self.dap, "DapAnnex", branch=self.dap_annex)

        self.assertEqual(
            identity_module.resolve_for_branch(self.dap_main).sender_id, "Dapgroup"
        )
        self.assertEqual(
            identity_module.resolve_for_branch(self.dap_annex).sender_id, "DapAnnex"
        )

    def test_a_branch_with_no_identity_of_its_own_uses_the_school_default(self):
        self.configure(self.dap, "Dapgroup")
        self.assertEqual(
            identity_module.resolve_for_branch(self.dap_annex).sender_id, "Dapgroup"
        )

    def test_no_config_at_all_refuses_with_a_named_school(self):
        with self.assertRaises(SenderIdentityUnavailable) as caught:
            identity_module.resolve_for_branch(self.dap_main)

        message = str(caught.exception)
        self.assertIn("Dap Group of Schools", message)
        self.assertIn("no Sender ID", message)
        self.assertIsNone(caught.exception.config)

    def test_a_pending_sender_id_refuses_and_says_so(self):
        self.configure(self.dap, "Dapgroup", status=SenderIdStatus.PENDING)

        with self.assertRaises(SenderIdentityUnavailable) as caught:
            identity_module.resolve_for_branch(self.dap_main)

        message = str(caught.exception)
        self.assertIn("Dapgroup", message)
        self.assertIn("awaiting approval", message)
        self.assertIsNotNone(caught.exception.config)

    def test_a_rejected_sender_id_carries_the_reason(self):
        self.configure(
            self.dap, "Dapgroup", status=SenderIdStatus.REJECTED,
            status_note="Gateway said the name is too close to a bank.",
        )

        with self.assertRaises(SenderIdentityUnavailable) as caught:
            identity_module.resolve_for_branch(self.dap_main)

        self.assertIn("too close to a bank", str(caught.exception))

    def test_a_suspended_sender_id_pauses_sending(self):
        self.configure(
            self.dap, "Dapgroup", status=SenderIdStatus.SUSPENDED,
            status_note="Unpaid units.",
        )
        with self.assertRaises(SenderIdentityUnavailable) as caught:
            identity_module.resolve_for_branch(self.dap_main)
        self.assertIn("suspended", str(caught.exception))

    def test_one_schools_identity_is_never_resolved_for_another(self):
        """The isolation that matters: Beta must not inherit Dap's name."""
        self.configure(self.dap, "Dapgroup")

        with self.assertRaises(SenderIdentityUnavailable) as caught:
            identity_module.resolve_for_branch(self.beta_main)

        self.assertIn("Beta College", str(caught.exception))
        self.assertNotIn("Dapgroup", str(caught.exception))


# ===========================================================================
# Sending
# ===========================================================================


class SendingUnderOwnIdentityTests(IdentityTestCase):
    def test_a_message_records_the_sender_id_it_went_out_under(self):
        self.configure(self.dap, "Dapgroup")

        message = self.send_from(self.dap_main)

        self.assertEqual(message.sent_as, "Dapgroup")
        self.assertEqual(message.recipients.count(), 1)
        self.assertEqual(
            message.delivery_counts()[DeliveryStatus.DELIVERED], 1
        )

    def test_the_console_provider_logs_the_sender_id_it_would_have_used(self):
        """The whole feature is verifiable with no credentials because of this."""
        self.configure(self.dap, "Dapgroup")

        with self.assertLogs("schoolcord.messaging", level="INFO") as logs:
            self.send_from(self.dap_main)

        line = "\n".join(logs.output)
        self.assertIn("Dapgroup", line)
        self.assertIn("Dap Group of Schools", line)
        # And which gateway would have carried it, not just "console".
        self.assertIn("Termii", line)

    def test_two_schools_send_under_their_own_names_in_the_same_run(self):
        self.configure(self.dap, "Dapgroup")
        self.configure(self.beta, "Betacol")

        with self.assertLogs("schoolcord.messaging", level="INFO") as logs:
            dap_message = self.send_from(self.dap_main)
            beta_message = self.send_from(self.beta_main)

        self.assertEqual(dap_message.sent_as, "Dapgroup")
        self.assertEqual(beta_message.sent_as, "Betacol")

        dap_line = next(l for l in logs.output if "Ada" not in l and "Dapgroup" in l)
        self.assertNotIn("Betacol", dap_line)

    def test_a_school_with_no_approved_sender_id_sends_nothing_at_all(self):
        """Not a failed batch -- no batch. Nothing was ever sendable."""
        self.configure(self.dap, "Dapgroup", status=SenderIdStatus.PENDING)

        with self.assertRaises(SenderIdentityUnavailable):
            self.send_from(self.dap_main)

        self.assertFalse(Message.all_objects.exists())

    def test_a_school_with_no_config_at_all_sends_nothing(self):
        with self.assertRaises(SenderIdentityUnavailable):
            self.send_from(self.dap_main)
        self.assertFalse(Message.all_objects.exists())

    def test_resuming_a_batch_re_resolves_the_identity(self):
        """An approval withdrawn mid-batch stops the rest of it."""
        config = self.configure(self.dap, "Dapgroup")
        with self.as_user(self.dap_bursar):
            audience = audiences.resolve(AudienceType.ALL, branch=self.dap_main)
            identity = identity_module.resolve_for_branch(self.dap_main)
            message = dispatch.record(
                body="Later.", channel=Channel.SMS, audience=audience,
                branch=self.dap_main, sender=self.dap_bursar, identity=identity,
            )

        config.status = SenderIdStatus.SUSPENDED
        config.save()

        with self.assertRaises(SenderIdentityUnavailable):
            dispatch.deliver(message)

    def test_the_provider_is_the_schools_own_when_there_is_no_override(self):
        self.configure(self.dap, "Dapgroup", provider=ProviderKey.BULKSMSNIGERIA)
        identity = identity_module.resolve_for_branch(self.dap_main)

        with override_settings(MESSAGING_PROVIDER=""):
            provider = get_provider(identity=identity)

        self.assertIsInstance(provider, BulkSMSNigeriaProvider)
        self.assertEqual(provider.sender_id, "Dapgroup")

    def test_the_platform_override_wins_over_the_schools_choice(self):
        self.configure(self.dap, "Dapgroup", provider=ProviderKey.BULKSMSNIGERIA)
        identity = identity_module.resolve_for_branch(self.dap_main)

        # The default in dev, and what every other test here runs on.
        provider = get_provider(identity=identity)

        self.assertIsInstance(provider, ConsoleProvider)
        # The school's own name still travels with it.
        self.assertEqual(provider.sender_id, "Dapgroup")


# ===========================================================================
# Providers
# ===========================================================================


class ProviderIdentityTests(TestCase):
    def test_a_real_provider_without_an_identity_refuses(self):
        with override_settings(BULKSMSNIGERIA_API_TOKEN="token"):
            provider = BulkSMSNigeriaProvider()
            self.assertFalse(provider.is_configured)
            with self.assertRaises(ProviderNotConfigured) as caught:
                provider.check()
        self.assertIn("Sender ID", str(caught.exception))

    def test_the_console_provider_needs_no_sender_id(self):
        self.assertTrue(ConsoleProvider().is_configured)

    def test_the_console_provider_says_when_no_identity_resolved(self):
        with self.assertLogs("schoolcord.messaging", level="INFO") as logs:
            ConsoleProvider().send("08031234567", "Hi", Channel.SMS)
        self.assertIn("no Sender ID resolved", "\n".join(logs.output))

    def test_missing_master_credentials_are_reported_separately(self):
        """A missing token is the platform's problem, not the school's."""
        identity = SenderIdentity(
            sender_id="Dapgroup", provider_key=ProviderKey.BULKSMSNIGERIA
        )
        with override_settings(BULKSMSNIGERIA_API_TOKEN=""):
            with self.assertRaises(ProviderNotConfigured) as caught:
                BulkSMSNigeriaProvider(identity).check()
        self.assertIn("BULKSMSNIGERIA_API_TOKEN", str(caught.exception))


@override_settings(BULKSMSNIGERIA_API_TOKEN="master-token")
class BulkSMSNigeriaTests(TestCase):
    """The real gateway, exercised without touching the network.

    The token is overridden for the whole class rather than per call, because
    ``api_token`` is read at send time, not at construction -- which is what
    lets a school's own key win over the platform's on the very same instance.
    """

    identity = SenderIdentity(
        sender_id="Dapgroup",
        provider_key=ProviderKey.BULKSMSNIGERIA,
        school_name="Dap Group of Schools",
    )

    def provider(self):
        provider = BulkSMSNigeriaProvider(self.identity)
        provider.check()
        return provider

    def test_the_payload_sends_the_schools_sender_id(self):
        payload = self.provider().payload("08031234567", "Fees are due.")

        self.assertEqual(payload["from"], "Dapgroup")
        self.assertEqual(payload["to"], "2348031234567")
        self.assertEqual(payload["body"], "Fees are due.")
        self.assertEqual(payload["dnd"], "2")

    def test_a_school_with_its_own_key_uses_it_over_the_platforms(self):
        own = SenderIdentity(
            sender_id="Dapgroup",
            provider_key=ProviderKey.BULKSMSNIGERIA,
            api_key="school-token",
        )
        self.assertEqual(BulkSMSNigeriaProvider(own).api_token, "school-token")
        self.assertEqual(
            BulkSMSNigeriaProvider(self.identity).api_token, "master-token"
        )

    def test_an_accepted_message_is_sent_not_delivered(self):
        """Their queue accepting it says nothing about a handset receiving it."""
        result = self.provider().read_response(
            '{"data": {"status": "success", "message_id": "abc123"}}'
        )
        self.assertEqual(result.status, DeliveryStatus.SENT)
        self.assertEqual(result.reference, "abc123")

    def test_a_200_carrying_an_error_is_a_failure(self):
        result = self.provider().read_response(
            '{"error": {"to": ["The number is invalid."]}}'
        )
        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertIn("The number is invalid.", result.error)

    def test_a_non_json_body_fails_rather_than_exploding(self):
        result = self.provider().read_response("<html>502</html>")
        self.assertEqual(result.status, DeliveryStatus.FAILED)

    def test_an_unregistered_sender_id_is_explained(self):
        message = self.provider().read_error(422, "{}")
        self.assertIn("Dapgroup", message)
        self.assertIn("registered", message)

    def test_bad_credentials_point_at_the_env_var(self):
        self.assertIn(
            "BULKSMSNIGERIA_API_TOKEN", self.provider().read_error(401, "{}")
        )

    def test_a_real_send_posts_the_schools_sender_id(self):
        provider = self.provider()
        captured = {}

        class FakeResponse:
            def read(self):
                return b'{"data": {"status": "success", "message_id": "xyz"}}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["body"] = request.data.decode()
            captured["headers"] = request.headers
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            result = provider.send("08031234567", "Fees are due.", Channel.SMS)

        self.assertEqual(result.status, DeliveryStatus.SENT)
        self.assertEqual(result.reference, "xyz")
        self.assertIn('"from": "Dapgroup"', captured["body"])
        self.assertIn("/api/v2/sms", captured["url"])
        self.assertEqual(
            captured["headers"]["Authorization"], "Bearer master-token"
        )

    def test_a_network_failure_becomes_a_failed_row_not_a_crash(self):
        import urllib.error

        provider = self.provider()

        def boom(request, timeout=None):
            raise urllib.error.URLError("connection refused")

        with mock.patch("urllib.request.urlopen", boom):
            result = provider.send("08031234567", "Hi", Channel.SMS)

        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertIn("connection refused", result.error)

    def test_it_does_not_claim_whatsapp(self):
        self.assertFalse(self.provider().supports(Channel.WHATSAPP))


# ===========================================================================
# Screens and permissions
# ===========================================================================


class IdentityCapabilityTests(TestCase):
    def test_leadership_may_view_their_schools_identity(self):
        for role in (Role.SCHOOL_OWNER, Role.PRINCIPAL, Role.PLATFORM_OWNER):
            with self.subTest(role=role):
                self.assertTrue(
                    has_capability(
                        User(role=role), Capability.VIEW_MESSAGING_IDENTITY
                    )
                )

    def test_only_the_platform_may_register_or_approve_one(self):
        self.assertTrue(
            has_capability(
                User(role=Role.PLATFORM_OWNER),
                Capability.MANAGE_MESSAGING_IDENTITY,
            )
        )
        for role in (Role.SCHOOL_OWNER, Role.PRINCIPAL, Role.BURSAR):
            with self.subTest(role=role):
                self.assertFalse(
                    has_capability(
                        User(role=role), Capability.MANAGE_MESSAGING_IDENTITY
                    )
                )


class IdentityScreenTests(IdentityTestCase):
    def test_a_school_owner_sees_their_own_sender_id(self):
        self.configure(self.dap, "Dapgroup")

        self.client.force_login(self.dap_owner)
        response = self.client.get(reverse("messaging:identity"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_platform_view"])
        self.assertEqual(response.context["active"].sender_id, "Dapgroup")
        self.assertContains(response, "Dapgroup")

    def test_a_school_never_sees_another_schools_sender_id(self):
        self.configure(self.dap, "Dapgroup")
        self.configure(self.beta, "Betacol")

        self.client.force_login(self.dap_owner)
        response = self.client.get(reverse("messaging:identity"))

        self.assertContains(response, "Dapgroup")
        self.assertNotContains(response, "Betacol")
        self.assertEqual(
            [c.school_id for c in response.context["configs"]], [self.dap.pk]
        )

    def test_a_school_with_none_is_told_what_that_means(self):
        self.client.force_login(self.dap_owner)
        response = self.client.get(reverse("messaging:identity"))

        self.assertIsNone(response.context["active"])
        self.assertContains(response, "No Sender ID registered yet")

    def test_a_school_owner_cannot_reach_the_edit_screen(self):
        self.client.force_login(self.dap_owner)
        response = self.client.get(
            reverse("messaging:identity_update", args=[self.dap.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_a_bursar_cannot_reach_the_identity_screen_at_all(self):
        self.client.force_login(self.dap_bursar)
        self.assertEqual(
            self.client.get(reverse("messaging:identity")).status_code, 403
        )

    def test_the_platform_owner_sees_every_school_including_unregistered(self):
        self.configure(self.dap, "Dapgroup")

        self.client.force_login(self.platform)
        response = self.client.get(reverse("messaging:identity"))

        self.assertTrue(response.context["is_platform_view"])
        rows = {row["school"].name: row["config"] for row in response.context["rows"]}
        self.assertEqual(rows["Dap Group of Schools"].sender_id, "Dapgroup")
        self.assertIsNone(rows["Beta College"])
        self.assertContains(response, "Not registered")

    def test_the_platform_owner_registers_and_approves_one(self):
        self.client.force_login(self.platform)
        response = self.client.post(
            reverse("messaging:identity_update", args=[self.dap.pk]),
            {
                "sender_id": "Dapgroup",
                "provider": ProviderKey.BULKSMSNIGERIA,
                "status": SenderIdStatus.APPROVED,
                "status_note": "",
                "api_key": "",
                "account_reference": "",
            },
        )

        self.assertRedirects(response, reverse("messaging:identity"))
        config = SchoolMessagingConfig.all_objects.get(school=self.dap)
        self.assertEqual(config.sender_id, "Dapgroup")
        self.assertTrue(config.is_usable)
        self.assertEqual(config.approved_by, self.platform)
        self.assertIsNone(config.branch_id)

    def test_editing_updates_rather_than_creating_a_second_row(self):
        self.configure(self.dap, "Dapgroup")

        self.client.force_login(self.platform)
        self.client.post(
            reverse("messaging:identity_update", args=[self.dap.pk]),
            {
                "sender_id": "DapSchool",
                "provider": ProviderKey.TERMII,
                "status": SenderIdStatus.APPROVED,
                "status_note": "",
                "api_key": "",
                "account_reference": "",
            },
        )

        self.assertEqual(
            SchoolMessagingConfig.all_objects.filter(school=self.dap).count(), 1
        )
        config = SchoolMessagingConfig.all_objects.get(school=self.dap)
        self.assertEqual(config.sender_id, "DapSchool")
        self.assertEqual(config.provider, ProviderKey.TERMII)


class IdentityFormTests(IdentityTestCase):
    def test_a_new_registration_defaults_to_termii(self):
        """What the settings screen offers a school that has not registered yet."""
        form = MessagingIdentityForm(school=self.dap)
        self.assertEqual(form.fields["provider"].initial, ProviderKey.TERMII)
        self.assertIn("Termii", form["provider"].as_widget())

    def test_another_gateway_can_still_be_chosen(self):
        """Termii is the default, not the only option -- that is the point."""
        form = MessagingIdentityForm(
            data=self.data(provider=ProviderKey.BULKSMSNIGERIA), school=self.dap
        )
        self.assertTrue(form.is_valid(), form.errors)
        config = form.save(user=self.platform)
        self.assertEqual(config.provider, ProviderKey.BULKSMSNIGERIA)

    def data(self, **overrides):
        payload = {
            "sender_id": "Dapgroup",
            "provider": ProviderKey.TERMII,
            "status": SenderIdStatus.APPROVED,
            "status_note": "",
            "api_key": "",
            "account_reference": "",
        }
        payload.update(overrides)
        return payload

    def test_two_schools_cannot_register_the_same_sender_id(self):
        """On one platform, parents could not tell which school texted them."""
        self.configure(self.beta, "Dapgroup")

        form = MessagingIdentityForm(data=self.data(), school=self.dap)

        self.assertFalse(form.is_valid())
        self.assertIn("Beta College", str(form.errors["sender_id"]))

    def test_the_same_check_is_case_insensitive(self):
        self.configure(self.beta, "Dapgroup")
        form = MessagingIdentityForm(data=self.data(sender_id="DAPGROUP"),
                                     school=self.dap)
        self.assertFalse(form.is_valid())

    def test_a_school_may_keep_its_own_sender_id_when_editing(self):
        config = self.configure(self.dap, "Dapgroup")
        form = MessagingIdentityForm(
            data=self.data(), instance=config, school=self.dap
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_rejection_must_say_why(self):
        form = MessagingIdentityForm(
            data=self.data(status=SenderIdStatus.REJECTED), school=self.dap
        )
        self.assertFalse(form.is_valid())
        self.assertIn("status_note", form.errors)

    def test_an_over_long_sender_id_is_refused_on_the_form(self):
        form = MessagingIdentityForm(
            data=self.data(sender_id="DapGroupOfSchools"), school=self.dap
        )
        self.assertFalse(form.is_valid())
        self.assertIn("sender_id", form.errors)

    def test_saving_an_unapproved_config_clears_any_stale_approval(self):
        config = self.configure(self.dap, "Dapgroup")
        config.approve(by=self.platform)

        form = MessagingIdentityForm(
            data=self.data(status=SenderIdStatus.SUSPENDED,
                           status_note="Unpaid units."),
            instance=config,
            school=self.dap,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save(user=self.platform)

        self.assertIsNone(saved.approved_at)
        self.assertIsNone(saved.approved_by)


class ComposeScreenTests(IdentityTestCase):
    def test_the_compose_screen_names_the_sender_id(self):
        self.configure(self.dap, "Dapgroup")

        self.client.force_login(self.dap_bursar)
        response = self.client.get(reverse("messaging:compose"))

        self.assertTrue(response.context["identity"]["resolved"])
        self.assertEqual(response.context["identity"]["sender_id"], "Dapgroup")
        self.assertContains(response, "Dapgroup")

    def test_the_compose_screen_explains_a_block_rather_than_erroring(self):
        self.configure(self.dap, "Dapgroup", status=SenderIdStatus.PENDING)

        self.client.force_login(self.dap_bursar)
        response = self.client.get(reverse("messaging:compose"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["identity"]["resolved"])
        self.assertContains(response, "awaiting approval")

    def test_sending_without_an_approved_sender_id_keeps_the_draft(self):
        self.configure(self.dap, "Dapgroup", status=SenderIdStatus.PENDING)

        self.client.force_login(self.dap_bursar)
        response = self.client.post(
            reverse("messaging:compose"),
            {
                "body": "School fees are due.",
                "channel": Channel.SMS,
                "audience_type": AudienceType.ALL,
                "branch": self.dap_main.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Message.all_objects.exists())
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertContains(response, "awaiting approval")

    def test_sending_with_one_goes_out_under_it(self):
        self.configure(self.dap, "Dapgroup")

        self.client.force_login(self.dap_bursar)
        response = self.client.post(
            reverse("messaging:compose"),
            {
                "body": "School fees are due.",
                "channel": Channel.SMS,
                "audience_type": AudienceType.ALL,
                "branch": self.dap_main.pk,
            },
        )

        message = Message.all_objects.get()
        self.assertRedirects(response, message.get_absolute_url())
        self.assertEqual(message.sent_as, "Dapgroup")

    def test_the_log_records_which_name_each_batch_went_out_under(self):
        self.configure(self.dap, "Dapgroup")
        message = self.send_from(self.dap_main)

        self.client.force_login(self.dap_bursar)
        response = self.client.get(message.get_absolute_url())

        self.assertContains(response, "Sent as")
        self.assertContains(response, "Dapgroup")


class IdentityScopingTests(IdentityTestCase):
    def test_the_scoped_manager_hides_another_schools_config(self):
        self.configure(self.dap, "Dapgroup")
        self.configure(self.beta, "Betacol")

        with self.as_user(self.dap_owner):
            self.assertEqual(
                [c.sender_id for c in SchoolMessagingConfig.objects.all()],
                ["Dapgroup"],
            )
        with self.as_user(self.beta_bursar):
            self.assertEqual(
                [c.sender_id for c in SchoolMessagingConfig.objects.all()],
                ["Betacol"],
            )

    def test_beta_cannot_send_under_daps_name_even_with_dap_configured(self):
        """The headline isolation test: identity does not leak across tenants."""
        self.configure(self.dap, "Dapgroup")

        with self.assertRaises(SenderIdentityUnavailable):
            self.send_from(self.beta_main)

        self.assertFalse(Message.all_objects.exists())

    def test_configuring_beta_gives_beta_its_own_name(self):
        self.configure(self.dap, "Dapgroup")
        self.configure(self.beta, "Betacol")

        message = self.send_from(self.beta_main)

        self.assertEqual(message.sent_as, "Betacol")
        self.assertEqual(message.school_id, self.beta.pk)

    def test_the_platform_edit_screen_is_addressed_per_school(self):
        """A platform owner editing Beta cannot touch Dap's row."""
        self.configure(self.dap, "Dapgroup")

        self.client.force_login(self.platform)
        self.client.post(
            reverse("messaging:identity_update", args=[self.beta.pk]),
            {
                "sender_id": "Betacol",
                "provider": ProviderKey.BULKSMSNIGERIA,
                "status": SenderIdStatus.APPROVED,
                "status_note": "",
                "api_key": "",
                "account_reference": "",
            },
        )

        self.assertEqual(
            SchoolMessagingConfig.all_objects.get(school=self.dap).sender_id,
            "Dapgroup",
        )
        self.assertEqual(
            SchoolMessagingConfig.all_objects.get(school=self.beta).sender_id,
            "Betacol",
        )


class NavigationTests(TestCase):
    def test_leadership_gets_the_sender_id_link(self):
        from apps.core.navigation import nav_for

        for role in (Role.SCHOOL_OWNER, Role.PRINCIPAL, Role.PLATFORM_OWNER):
            sections = {s.label: s.items for s in nav_for(role, "/messaging/")}
            labels = {i.label: i for i in sections["Communication"]}
            with self.subTest(role=role):
                self.assertIn("Sender ID", labels)
                self.assertTrue(labels["Sender ID"].available)

    def test_a_bursar_does_not(self):
        from apps.core.navigation import nav_for

        sections = {s.label: s.items for s in nav_for(Role.BURSAR, "/messaging/")}
        labels = {i.label for i in sections["Communication"]}
        self.assertNotIn("Sender ID", labels)


class SeedCommandTests(IdentityTestCase):
    def seed(self, *args):
        out = StringIO()
        call_command("seed_messaging_identity", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_it_registers_and_approves_the_named_school(self):
        output = self.seed(
            "--school", "Dap Group of Schools", "--sender-id", "Dapgroup"
        )

        config = SchoolMessagingConfig.all_objects.get(school=self.dap)
        self.assertEqual(config.sender_id, "Dapgroup")
        # Termii, since BulkSMS Nigeria declined the send-on-behalf model.
        self.assertEqual(config.provider, ProviderKey.TERMII)
        self.assertTrue(config.is_usable)
        self.assertIn("approved", output)

    def test_pending_seeds_the_blocked_state(self):
        self.seed("--school", "Dap Group of Schools", "--sender-id", "Dapgroup",
                  "--pending")

        config = SchoolMessagingConfig.all_objects.get(school=self.dap)
        self.assertFalse(config.is_usable)

    def test_running_it_twice_updates_rather_than_duplicating(self):
        self.seed("--school", "Dap Group of Schools", "--sender-id", "Dapgroup")
        self.seed("--school", "Dap Group of Schools", "--sender-id", "DapSchool")

        configs = SchoolMessagingConfig.all_objects.filter(school=self.dap)
        self.assertEqual(configs.count(), 1)
        self.assertEqual(configs.first().sender_id, "DapSchool")

    def test_an_invalid_sender_id_is_refused_with_the_reason(self):
        with self.assertRaises(CommandError) as caught:
            self.seed("--school", "Dap Group of Schools",
                      "--sender-id", "DapGroupOfSchools")
        self.assertIn("11 characters", str(caught.exception))

    def test_it_will_not_hand_one_school_anothers_name(self):
        self.configure(self.beta, "Dapgroup")

        with self.assertRaises(CommandError) as caught:
            self.seed("--school", "Dap Group of Schools", "--sender-id", "Dapgroup")

        self.assertIn("Beta College", str(caught.exception))

    def test_an_unknown_school_is_a_clear_error(self):
        with self.assertRaises(CommandError):
            self.seed("--school", "Nowhere Academy")


# ===========================================================================
# Termii -- the gateway
# ===========================================================================


class FakeResponse:
    """Enough of an ``http.client.HTTPResponse`` for ``urlopen``'s context use."""

    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RecordingTransport:
    """Stands in for ``urllib.request.urlopen`` and keeps what it was given.

    The provider is tested against this rather than against the network for the
    obvious reason, and against a recorded request rather than a mocked method
    so the assertions are about *what Termii would receive* -- which is the only
    thing that decides whether a parent gets the message.
    """

    def __init__(self, body='{"code": "ok", "message_id": "9122821270554876574", '
                            '"message": "Successfully Sent", "balance": 412}',
                 error=None):
        self.body = body
        self.error = error
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return FakeResponse(self.body)

    # -- what was actually sent --------------------------------------------

    @property
    def payload(self) -> dict:
        return json.loads(self.requests[-1].data.decode("utf-8"))

    @property
    def url(self) -> str:
        return self.requests[-1].full_url

    @property
    def payloads(self) -> list[dict]:
        return [json.loads(r.data.decode("utf-8")) for r in self.requests]


@override_settings(TERMII_API_KEY="master-key")
class TermiiProviderTests(TestCase):
    """The live gateway, exercised without touching the network.

    The key is overridden for the whole class rather than per call, because
    ``api_key`` is read at send time, not at construction -- which is what lets
    a school's own key win over the platform's on the very same instance.
    """

    identity = SenderIdentity(
        sender_id="Fulfilled",
        provider_key=ProviderKey.TERMII,
        school_name="Fulfilled Academy",
    )

    def provider(self, identity=None):
        return TermiiProvider(identity or self.identity)

    def send(self, transport, **kwargs):
        options = {
            "recipient": "08031234567",
            "message": "Fees are due.",
            "channel": Channel.SMS,
        }
        options.update(kwargs)
        with mock.patch("urllib.request.urlopen", transport):
            return self.provider(options.pop("identity", None)).send(
                options.pop("recipient"),
                options.pop("message"),
                options.pop("channel"),
                **options,
            )

    # -- credentials --------------------------------------------------------

    @override_settings(TERMII_API_KEY="")
    def test_without_credentials_it_is_not_configured(self):
        provider = self.provider()
        self.assertFalse(provider.is_configured)
        with self.assertRaises(ProviderNotConfigured) as caught:
            provider.check()
        self.assertIn("TERMII_API_KEY", str(caught.exception))

    def test_without_a_sender_id_it_refuses(self):
        """A missing Sender ID is the school's problem and is named first."""
        with self.assertRaises(ProviderNotConfigured) as caught:
            TermiiProvider().check()
        self.assertIn("Sender ID", str(caught.exception))

    def test_a_school_with_its_own_key_uses_it_over_the_platforms(self):
        own = SenderIdentity(
            sender_id="Fulfilled",
            provider_key=ProviderKey.TERMII,
            api_key="school-key",
        )
        self.assertEqual(TermiiProvider(own).api_key, "school-key")
        self.assertEqual(self.provider().api_key, "master-key")

    def test_blank_school_credentials_fall_through_to_the_master_account(self):
        """The pilot arrangement, asserted rather than assumed."""
        transport = RecordingTransport()
        self.send(transport)
        self.assertEqual(transport.payload["api_key"], "master-key")

    # -- the request --------------------------------------------------------

    def test_it_posts_to_the_termii_send_endpoint(self):
        transport = RecordingTransport()
        self.send(transport)
        self.assertEqual(transport.url, "https://api.ng.termii.com/api/sms/send")
        self.assertEqual(transport.requests[-1].get_method(), "POST")

    def test_the_message_goes_out_under_the_schools_own_sender_id(self):
        transport = RecordingTransport()
        self.send(transport)

        payload = transport.payload
        self.assertEqual(payload["from"], "Fulfilled")
        self.assertEqual(payload["to"], "2348031234567")
        self.assertEqual(payload["sms"], "Fees are due.")
        self.assertEqual(payload["type"], "plain")

    def test_a_second_school_sends_under_its_own_name_not_the_first(self):
        """Two identities, two requests, two different senders."""
        transport = RecordingTransport()
        beta = SenderIdentity(
            sender_id="Betacol",
            provider_key=ProviderKey.TERMII,
            school_name="Beta College",
        )
        self.send(transport)
        self.send(transport, identity=beta)

        self.assertEqual([p["from"] for p in transport.payloads],
                         ["Fulfilled", "Betacol"])

    # -- routing ------------------------------------------------------------

    def test_school_messaging_defaults_to_the_dnd_route(self):
        """The default has to be the route that reaches DND numbers."""
        transport = RecordingTransport()
        self.send(transport)
        self.assertEqual(transport.payload["channel"], "dnd")

    def test_a_transactional_message_uses_the_dnd_route(self):
        transport = RecordingTransport()
        self.send(transport, purpose=MessagePurpose.TRANSACTIONAL)
        self.assertEqual(transport.payload["channel"], "dnd")

    def test_only_a_promotional_message_uses_the_generic_route(self):
        transport = RecordingTransport()
        self.send(transport, purpose=MessagePurpose.PROMOTIONAL)
        self.assertEqual(transport.payload["channel"], "generic")

    def test_an_unrecognised_purpose_falls_to_dnd_not_generic(self):
        """Fail towards the route that arrives.

        A purpose added later and not thought about here, a blank, or a typo
        must not silently drop a school's messages for every DND parent.
        """
        provider = self.provider()
        for purpose in ("", "urgent", None, "TRANSACTIONAL"):
            with self.subTest(purpose=purpose):
                self.assertEqual(
                    provider.termii_channel(Channel.SMS, purpose), "dnd"
                )

    def test_whatsapp_has_its_own_channel_whatever_the_purpose(self):
        provider = self.provider()
        for purpose in (MessagePurpose.TRANSACTIONAL, MessagePurpose.PROMOTIONAL):
            with self.subTest(purpose=purpose):
                self.assertEqual(
                    provider.termii_channel(Channel.WHATSAPP, purpose), "whatsapp"
                )

    # -- their answers ------------------------------------------------------

    def test_an_accepted_message_is_sent_not_delivered(self):
        """Their queue accepting it says nothing about a handset receiving it."""
        result = self.send(RecordingTransport())
        self.assertEqual(result.status, DeliveryStatus.SENT)
        self.assertEqual(result.reference, "9122821270554876574")
        self.assertEqual(result.error, "")
        self.assertTrue(result.ok)

    def test_the_single_send_shape_without_a_code_is_still_a_success(self):
        body = '{"message_id": "abc123", "message": "Successfully Sent", "balance": 9}'
        result = self.provider().read_response(body)
        self.assertEqual(result.status, DeliveryStatus.SENT)
        self.assertEqual(result.reference, "abc123")

    def test_a_code_that_is_not_ok_is_a_failure_with_the_reason(self):
        body = '{"code": "invalid_sender", "message": "Sender ID not registered"}'
        result = self.provider().read_response(body)
        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertIn("Sender ID not registered", result.error)
        self.assertEqual(result.reference, "")

    def test_a_success_with_no_message_id_is_refused_not_recorded(self):
        """Nothing to reconcile a later delivery report against."""
        result = self.provider().read_response('{"code": "ok"}')
        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertIn("message id", result.error.lower())

    def test_a_non_json_body_is_a_clear_failure_not_a_crash(self):
        result = self.provider().read_response("<html>502 Bad Gateway</html>")
        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertIn("not JSON", result.error)

    def test_an_http_error_carries_termiis_own_explanation(self):
        error = urllib.error.HTTPError(
            "https://api.ng.termii.com/api/sms/send", 400, "Bad Request", {},
            io.BytesIO(b'{"message": "Insufficient balance"}'),
        )
        result = self.send(RecordingTransport(error=error))
        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertIn("Insufficient balance", result.error)

    def test_bad_credentials_name_the_setting_to_fix(self):
        error = urllib.error.HTTPError(
            "https://api.ng.termii.com/api/sms/send", 401, "Unauthorized", {},
            io.BytesIO(b"{}"),
        )
        result = self.send(RecordingTransport(error=error))
        self.assertIn("TERMII_API_KEY", result.error)

    def test_an_unreachable_gateway_is_a_failed_row_not_an_exception(self):
        result = self.send(
            RecordingTransport(error=urllib.error.URLError("no route to host"))
        )
        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertIn("Could not reach Termii", result.error)

    def test_a_timeout_is_a_failed_row_not_an_exception(self):
        result = self.send(RecordingTransport(error=TimeoutError()))
        self.assertEqual(result.status, DeliveryStatus.FAILED)
        self.assertIn("did not answer", result.error)

    def test_the_log_masks_the_parents_number(self):
        transport = RecordingTransport()
        with self.assertLogs("schoolcord.messaging", level="INFO") as logs:
            self.send(transport)
        line = "\n".join(logs.output)
        self.assertIn("...4567", line)
        self.assertNotIn("2348031234567", line)

    def test_a_low_balance_is_warned_about(self):
        """Units running out stops every school at once; it should not be quiet."""
        body = '{"code": "ok", "message_id": "x", "balance": 3}'
        with self.assertLogs("schoolcord.messaging", level="WARNING") as logs:
            self.provider().read_response(body)
        self.assertIn("balance", "\n".join(logs.output).lower())


@override_settings(TERMII_API_KEY="master-key", MESSAGING_PROVIDER="termii")
class TermiiDispatchTests(IdentityTestCase):
    """End to end: a real batch, through the real provider, over a fake socket.

    This is the test that would catch the Sender ID being dropped somewhere
    between the school's config and the wire -- which is the failure that
    matters most, because it is invisible until a parent asks why an unknown
    number is texting them about fees.
    """

    def test_a_batch_goes_out_under_that_schools_sender_id_on_the_dnd_route(self):
        self.configure(self.dap, "Dapgroup", provider=ProviderKey.TERMII)
        transport = RecordingTransport()

        with mock.patch("urllib.request.urlopen", transport):
            message = self.send_from(self.dap_main)

        self.assertTrue(transport.payloads)
        for payload in transport.payloads:
            self.assertEqual(payload["from"], "Dapgroup")
            self.assertEqual(payload["channel"], "dnd")
        self.assertEqual(message.sent_as, "Dapgroup")
        self.assertEqual(message.provider, ProviderKey.TERMII)

    def test_the_provider_reference_lands_on_every_recipient_row(self):
        self.configure(self.dap, "Dapgroup", provider=ProviderKey.TERMII)
        transport = RecordingTransport()

        with mock.patch("urllib.request.urlopen", transport):
            message = self.send_from(self.dap_main)

        rows = list(message.recipients.all())
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row.status, DeliveryStatus.SENT)
            self.assertEqual(row.provider_reference, "9122821270554876574")
            self.assertEqual(row.error, "")
            self.assertIsNotNone(row.sent_at)

    def test_a_refusal_lands_on_the_row_as_a_readable_error(self):
        self.configure(self.dap, "Dapgroup", provider=ProviderKey.TERMII)
        transport = RecordingTransport(
            body='{"code": "invalid_sender", "message": "Sender ID not registered"}'
        )

        with mock.patch("urllib.request.urlopen", transport):
            message = self.send_from(self.dap_main)

        for row in message.recipients.all():
            self.assertEqual(row.status, DeliveryStatus.FAILED)
            self.assertIn("Sender ID not registered", row.error)
            self.assertEqual(row.provider_reference, "")
            self.assertIsNone(row.sent_at)
        self.assertEqual(message.status, MessageStatus.FAILED)

    def test_two_schools_in_one_run_never_borrow_each_others_sender_id(self):
        """The tenancy breach a parent would see on their own handset."""
        self.configure(self.dap, "Dapgroup", provider=ProviderKey.TERMII)
        self.configure(self.beta, "Betacol", provider=ProviderKey.TERMII)
        transport = RecordingTransport()

        with mock.patch("urllib.request.urlopen", transport):
            self.send_from(self.dap_main)
            dap_requests = len(transport.payloads)
            self.send_from(self.beta_main)

        senders = [p["from"] for p in transport.payloads]
        self.assertTrue(dap_requests)
        self.assertTrue(set(senders[:dap_requests]) == {"Dapgroup"})
        self.assertTrue(set(senders[dap_requests:]) == {"Betacol"})

    def test_a_school_with_no_approved_sender_id_never_reaches_the_gateway(self):
        self.configure(
            self.dap, "Dapgroup",
            provider=ProviderKey.TERMII, status=SenderIdStatus.PENDING,
        )
        transport = RecordingTransport()

        with mock.patch("urllib.request.urlopen", transport):
            with self.assertRaises(SenderIdentityUnavailable):
                self.send_from(self.dap_main)

        self.assertEqual(transport.payloads, [])

    def test_a_promotional_batch_is_the_only_thing_on_the_generic_route(self):
        self.configure(self.dap, "Dapgroup", provider=ProviderKey.TERMII)
        transport = RecordingTransport()

        with mock.patch("urllib.request.urlopen", transport):
            with self.as_user(self.user_for(self.dap_main)):
                audience = audiences.resolve(AudienceType.ALL, branch=self.dap_main)
                dispatch.send(
                    body="Open day this Saturday.",
                    channel=Channel.SMS,
                    purpose=MessagePurpose.PROMOTIONAL,
                    audience=audience,
                    branch=self.dap_main,
                )

        for payload in transport.payloads:
            self.assertEqual(payload["channel"], "generic")

    def test_a_resumed_batch_keeps_the_route_it_was_recorded_with(self):
        """Half a batch on one route and half on another is not a thing."""
        self.configure(self.dap, "Dapgroup", provider=ProviderKey.TERMII)

        with self.as_user(self.user_for(self.dap_main)):
            audience = audiences.resolve(AudienceType.ALL, branch=self.dap_main)
            identity = identity_module.resolve_for_branch(self.dap_main)
            message = dispatch.record(
                body="Open day this Saturday.",
                channel=Channel.SMS,
                purpose=MessagePurpose.PROMOTIONAL,
                audience=audience,
                branch=self.dap_main,
                identity=identity,
                provider_key=ProviderKey.TERMII,
            )

        transport = RecordingTransport()
        with mock.patch("urllib.request.urlopen", transport):
            dispatch.deliver(message)

        self.assertTrue(transport.payloads)
        for payload in transport.payloads:
            self.assertEqual(payload["channel"], "generic")
