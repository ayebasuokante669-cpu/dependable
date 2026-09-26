"""The school's own admission requirements, edited from a screen.

They were always data rather than a chain of ``if level ==``, but the only way
to change them was the Django admin -- which the proprietor's account cannot
open. So the policy belonged to whoever had a shell, which is not who it
belongs to.

The line these tests hold is the one the client drew: a principal runs the
pipeline *under* the rules and cannot rewrite them; the proprietor writes them.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.academics.models import Class, Level
from apps.core.roles import Role
from apps.schools.models import Branch, School

from .models import Requirement, RequirementSet
from .requirements import RequirementKind, default_profile, profile_for

User = get_user_model()

#: Nursery, which by default asks for three documents and no exam.
NURSERY = Level.NURSERY


def payload(**overrides) -> dict:
    """A full form post: every kind has to carry a state."""
    data = {
        RequirementKind.PASSPORT_PHOTO: "required",
        RequirementKind.BIRTH_CERTIFICATE: "required",
        RequirementKind.PREVIOUS_RESULTS: "not_asked",
        RequirementKind.TRANSFER_LETTER: "not_asked",
        RequirementKind.IMMUNISATION_RECORD: "optional",
        RequirementKind.ENTRANCE_EXAM: "not_asked",
    }
    data.update(overrides)
    return data


class RequirementEditingTestCase(TestCase):
    @staticmethod
    def enable_admissions(*schools):
        """See AdmissionsTestCase.enable_admissions -- admissions is off by
        default, and these are schools that have bought it."""
        from apps.schools.models import SchoolModule

        for school in schools:
            SchoolModule.set_state(school, "admissions", True)

    @classmethod
    def setUpTestData(cls):
        cls.school = School.all_objects.create(name="Fulfilled Academy")
        cls.branch = Branch.all_objects.create(school=cls.school, name="Main Campus")
        cls.other_school = School.all_objects.create(name="Rival Academy")
        cls.other_branch = Branch.all_objects.create(
            school=cls.other_school, name="Their Campus"
        )
        cls.enable_admissions(cls.school, cls.other_school)
        cls.owner = User.objects.create_user(
            "owner", email="owner@example.com", password="pw",
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
        cls.other_owner = User.objects.create_user(
            "rival-owner", email="rival@example.com", password="pw",
            role=Role.SCHOOL_OWNER, school=cls.other_school,
        )
        cls.edit_url = reverse("admissions:requirement_update", args=[NURSERY])
        cls.reset_url = reverse("admissions:requirement_reset", args=[NURSERY])
        cls.list_url = reverse("admissions:requirements")


class OwnerEditsTheirOwnPolicyTests(RequirementEditingTestCase):
    def setUp(self):
        self.client.force_login(self.owner)

    def test_the_screen_opens_on_what_is_in_force(self):
        response = self.client.get(self.edit_url)
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        # Nursery's default: photo and birth certificate required, no exam.
        self.assertEqual(
            form.fields[RequirementKind.PASSPORT_PHOTO].initial, "required"
        )
        self.assertEqual(
            form.fields[RequirementKind.ENTRANCE_EXAM].initial, "not_asked"
        )

    def test_saving_writes_the_school_wide_set(self):
        response = self.client.post(self.edit_url, payload())
        self.assertEqual(response.status_code, 302)

        configured = RequirementSet.all_objects.get(school=self.school, level=NURSERY)
        # School-wide, not pinned to the campus the owner happens to sit at.
        self.assertIsNone(configured.branch_id)
        kinds = {r.kind: r.is_required for r in configured.requirements.all()}
        self.assertEqual(
            kinds,
            {
                RequirementKind.PASSPORT_PHOTO: True,
                RequirementKind.BIRTH_CERTIFICATE: True,
                RequirementKind.IMMUNISATION_RECORD: False,
            },
        )

    def test_the_saved_policy_is_what_the_pipeline_then_uses(self):
        self.client.post(
            self.edit_url,
            payload(**{RequirementKind.ENTRANCE_EXAM: "required"}),
        )
        profile = profile_for(NURSERY, school=self.school, branch=self.branch)
        self.assertTrue(profile.is_configured)
        # Nursery sat no exam by default; this school now says it does.
        self.assertTrue(profile.requires_assessment)
        self.assertFalse(default_profile(NURSERY).requires_assessment)

    def test_optional_and_not_asked_are_different_answers(self):
        self.client.post(self.edit_url, payload())
        profile = profile_for(NURSERY, school=self.school)
        optional = {line.kind for line in profile.optional_documents}
        every = {line.kind for line in profile.lines}
        self.assertIn(RequirementKind.IMMUNISATION_RECORD, optional)
        # "Not asked" is absent entirely, not present-and-optional.
        self.assertNotIn(RequirementKind.PREVIOUS_RESULTS, every)

    def test_saving_twice_rewrites_rather_than_accumulating(self):
        self.client.post(self.edit_url, payload())
        self.client.post(
            self.edit_url, payload(**{RequirementKind.IMMUNISATION_RECORD: "not_asked"})
        )
        self.assertEqual(RequirementSet.all_objects.filter(school=self.school).count(), 1)
        self.assertEqual(
            Requirement.all_objects.filter(requirement_set__school=self.school).count(), 2
        )

    def test_a_policy_asking_for_nothing_is_refused(self):
        response = self.client.post(
            self.edit_url, {kind: "not_asked" for kind in payload()}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(RequirementSet.all_objects.filter(school=self.school).exists())
        self.assertContains(response, "at least one thing")

    def test_reset_restores_the_platform_default(self):
        self.client.post(self.edit_url, payload())
        self.assertTrue(profile_for(NURSERY, school=self.school).is_configured)

        response = self.client.post(self.reset_url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(RequirementSet.all_objects.filter(school=self.school).exists())
        after = profile_for(NURSERY, school=self.school)
        self.assertFalse(after.is_configured)
        self.assertEqual(after.lines, default_profile(NURSERY).lines)

    def test_reset_cannot_be_triggered_by_following_a_link(self):
        self.client.post(self.edit_url, payload())
        response = self.client.get(self.reset_url)
        self.assertEqual(response.status_code, 405)
        self.assertTrue(RequirementSet.all_objects.filter(school=self.school).exists())

    def test_an_unknown_level_is_a_404(self):
        self.assertEqual(
            self.client.get(
                reverse("admissions:requirement_update", args=[99])
            ).status_code,
            404,
        )


class OnlyTheOwnerWritesThePolicyTests(RequirementEditingTestCase):
    def test_a_principal_reads_but_cannot_edit(self):
        self.client.force_login(self.principal)
        self.assertEqual(self.client.get(self.list_url).status_code, 200)
        self.assertEqual(self.client.get(self.edit_url).status_code, 403)
        self.assertEqual(self.client.post(self.edit_url, payload()).status_code, 403)
        self.assertEqual(self.client.post(self.reset_url).status_code, 403)

    def test_a_bursar_cannot_edit_either(self):
        self.client.force_login(self.bursar)
        self.assertEqual(self.client.get(self.edit_url).status_code, 403)
        self.assertEqual(self.client.post(self.edit_url, payload()).status_code, 403)

    def test_a_refused_post_writes_nothing(self):
        self.client.force_login(self.principal)
        self.client.post(self.edit_url, payload())
        self.assertFalse(RequirementSet.all_objects.exists())

    def test_a_signed_out_visitor_is_sent_to_sign_in(self):
        response = self.client.get(self.edit_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_the_platform_owner_holds_the_capability(self):
        from apps.core.permissions import Capability, capabilities_for

        self.assertIn(
            Capability.MANAGE_ADMISSION_REQUIREMENTS,
            capabilities_for(Role.PLATFORM_OWNER),
        )
        self.assertNotIn(
            Capability.MANAGE_ADMISSION_REQUIREMENTS,
            capabilities_for(Role.PRINCIPAL),
        )
        self.assertNotIn(
            Capability.MANAGE_ADMISSION_REQUIREMENTS, capabilities_for(Role.BURSAR)
        )


class RequirementsAreTenantScopedTests(RequirementEditingTestCase):
    def test_one_schools_policy_does_not_move_another(self):
        self.client.force_login(self.owner)
        self.client.post(
            self.edit_url, payload(**{RequirementKind.ENTRANCE_EXAM: "required"})
        )

        # The rival school still runs on the defaults.
        rival = profile_for(NURSERY, school=self.other_school)
        self.assertFalse(rival.is_configured)
        self.assertFalse(rival.requires_assessment)

    def test_an_owner_only_ever_writes_their_own_schools_set(self):
        self.client.force_login(self.other_owner)
        self.client.post(self.edit_url, payload())
        written = RequirementSet.all_objects.get()
        self.assertEqual(written.school_id, self.other_school.pk)

    def test_reset_cannot_delete_another_schools_policy(self):
        # Fulfilled configures Nursery...
        self.client.force_login(self.owner)
        self.client.post(self.edit_url, payload())
        # ...and the rival owner's reset must not touch it.
        self.client.force_login(self.other_owner)
        self.client.post(self.reset_url)
        self.assertTrue(
            RequirementSet.all_objects.filter(school=self.school).exists()
        )

    def test_the_list_shows_the_callers_own_policy(self):
        self.client.force_login(self.owner)
        self.client.post(
            self.edit_url, payload(**{RequirementKind.ENTRANCE_EXAM: "required"})
        )
        response = self.client.get(self.list_url)
        self.assertContains(response, "Your own")

        self.client.force_login(self.other_owner)
        response = self.client.get(self.list_url)
        self.assertNotContains(response, "Your own")


class ConfiguredRequirementsReachTheApplicationTests(RequirementEditingTestCase):
    """The policy is only worth editing if the rest of the system reads it."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.kg = Class.all_objects.create(
            school=cls.school, branch=cls.branch, name="KG 1",
            level=Level.NURSERY, year_in_level=1,
        )

    def test_a_level_that_now_sits_an_exam_says_so_on_the_applicant(self):
        from .models import Applicant

        self.client.force_login(self.owner)
        self.client.post(
            self.edit_url, payload(**{RequirementKind.ENTRANCE_EXAM: "required"})
        )
        applicant = Applicant.all_objects.create(
            school=self.school, branch=self.branch, first_name="Ada",
            last_name="Obi", level=Level.NURSERY, school_class=self.kg,
            parent_name="Mrs Obi", parent_phone="08031234567",
        )
        self.assertTrue(applicant.requirement_profile.requires_assessment)

    def test_a_document_no_longer_asked_for_stops_blocking_a_decision(self):
        from .models import Applicant

        applicant = Applicant.all_objects.create(
            school=self.school, branch=self.branch, first_name="Ada",
            last_name="Obi", level=Level.NURSERY, school_class=self.kg,
            parent_name="Mrs Obi", parent_phone="08031234567",
        )
        before = {line.kind for line in applicant.missing_requirements}
        self.assertIn(RequirementKind.IMMUNISATION_RECORD, before)

        self.client.force_login(self.owner)
        self.client.post(
            self.edit_url, payload(**{RequirementKind.IMMUNISATION_RECORD: "not_asked"})
        )
        # Re-fetched rather than refreshed: the profile is a cached property,
        # and refresh_from_db does not clear it.
        applicant = Applicant.all_objects.get(pk=applicant.pk)
        after = {line.kind for line in applicant.missing_requirements}
        self.assertNotIn(RequirementKind.IMMUNISATION_RECORD, after)
