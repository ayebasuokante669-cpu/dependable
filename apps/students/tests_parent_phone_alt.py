"""The optional second parent / guardian phone number.

Optional on the form and in the import, shown on the student's page, and never
the number a fee reminder goes to.
"""

from __future__ import annotations

from io import BytesIO

from django.urls import reverse
from openpyxl import load_workbook

from apps.messaging import audiences, dispatch
from apps.messaging.models import AudienceType, Channel, MessageRecipient

from . import importer
from .models import Student
from .tests import StudentTestCase, student_post
from .tests_import import ImportTestCase, build_xlsx, import_row


class FormTests(StudentTestCase):
    def setUp(self):
        self.client.force_login(self.north_principal)

    def create(self, **overrides):
        return self.client.post(
            reverse("students:student_create"),
            student_post(school_class=self.north_p1.pk, **overrides),
        )

    def test_it_is_optional(self):
        response = self.create()
        self.assertEqual(response.status_code, 302)
        student = Student.all_objects.get(admission_number="FA/2025/099")
        self.assertEqual(student.parent_phone_alt, "")

    def test_it_saves_in_the_canonical_form(self):
        self.create(parent_phone_alt="+234 805 987 6543")
        student = Student.all_objects.get(admission_number="FA/2025/099")
        self.assertEqual(student.parent_phone_alt, "08059876543")
        self.assertEqual(student.parent_phone, "08034129876")

    def test_a_bad_number_is_refused_on_the_field(self):
        response = self.create(parent_phone_alt="12")
        self.assertEqual(response.status_code, 200)
        self.assertIn("parent_phone_alt", response.context["form"].errors)
        self.assertFalse(
            Student.all_objects.filter(admission_number="FA/2025/099").exists()
        )

    def test_the_primary_number_is_still_required(self):
        response = self.create(parent_phone="", parent_phone_alt="08059876543")
        self.assertEqual(response.status_code, 200)
        self.assertIn("parent_phone", response.context["form"].errors)

    def test_edit_form_offers_and_saves_it(self):
        url = reverse("students:student_update", args=[self.chinaza.pk])
        self.assertContains(self.client.get(url), 'name="parent_phone_alt"')
        self.client.post(
            url,
            student_post(
                school_class=self.north_p1.pk,
                admission_number=self.chinaza.admission_number,
                parent_phone_alt="09012345678",
            ),
        )
        self.chinaza.refresh_from_db()
        self.assertEqual(self.chinaza.parent_phone_alt, "09012345678")


class DetailTests(StudentTestCase):
    def test_the_detail_page_shows_it(self):
        Student.all_objects.filter(pk=self.chinaza.pk).update(
            parent_phone_alt="08059876543"
        )
        self.client.force_login(self.north_principal)
        response = self.client.get(self.chinaza.get_absolute_url())
        self.assertContains(response, "Second phone")
        self.assertContains(response, "0805 987 6543")
        self.assertContains(response, 'href="tel:+2348059876543"')

    def test_not_recorded_when_blank(self):
        self.client.force_login(self.north_principal)
        response = self.client.get(self.emeka.get_absolute_url())
        self.assertContains(response, "Second phone")


class ImportTests(ImportTestCase):
    def test_the_template_has_it_as_an_optional_column(self):
        response = self.client.get(reverse("students:student_import_template"))
        headers = [c.value for c in load_workbook(BytesIO(response.content))["Students"][1]]
        self.assertIn("Second Parent / Guardian Phone", headers)  # no asterisk
        self.assertNotIn("Second Parent / Guardian Phone *", headers)
        self.assertNotIn("parent_phone_alt", importer.REQUIRED_KEYS)

    def test_a_row_without_it_imports(self):
        report = self.validate([import_row()])
        self.assertEqual(len(report.ready), 1)
        self.assertEqual(report.ready[0].student.parent_phone_alt, "")

    def test_a_row_with_it_imports_and_regains_its_leading_zero(self):
        self.post_file(build_xlsx([import_row(parent_phone_alt="8059876543")]))
        self.confirm()
        student = self.imported().get()
        self.assertEqual(student.parent_phone_alt, "08059876543")

    def test_a_schools_own_header_spelling_is_recognised(self):
        self.assertEqual(importer.column_for_header("Alternative Phone"), "parent_phone_alt")
        self.assertEqual(importer.column_for_header("Phone 2"), "parent_phone_alt")
        # The bare word still means the primary number.
        self.assertEqual(importer.column_for_header("Phone"), "parent_phone")

    def test_a_bad_second_number_refuses_the_row(self):
        report = self.validate([import_row(parent_phone_alt="12")])
        self.assertEqual(len(report.ready), 0)


class ReminderTests(StudentTestCase):
    def test_reminders_go_to_the_primary_number_only(self):
        Student.all_objects.filter(pk=self.chinaza.pk).update(
            parent_phone_alt="08059876543"
        )
        with self.as_user(self.north_bursar):
            audience = audiences.resolve(
                AudienceType.CLASS, branch=self.north, school_class=self.north_p1
            )
            # `record` is the step that decides each recipient's number;
            # `send` adds the Sender ID check and delivery on top of it.
            message = dispatch.record(
                body="Fees are due.",
                channel=Channel.SMS,
                audience=audience,
                branch=self.north,
                sender=self.north_bursar,
            )
        phones = list(
            MessageRecipient.all_objects.filter(message=message).values_list(
                "phone", flat=True
            )
        )
        self.assertEqual(phones, ["08034129876"])
