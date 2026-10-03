"""Tests for the class and subject imports.

The rules that must never regress are the ones the student import established --
a bad row is reported rather than dropped, a bad row does not take the good rows
down with it, the write is all-or-nothing, records are stamped to the branch the
importer chose, and a bursar cannot reach any of it -- plus the two that are new
here:

* a class name with no band in it is **refused**, not filed under a guess;
* a subject's "Taught In" cell resolves a level name to that level's classes,
  and names the ones it could not resolve rather than silently dropping them.

The fixture is :class:`apps.academics.tests.AcademicsTestCase`: one school, two
branches each running a "JSS 1", a second school entirely, and Mathematics
already taught at North.
"""

from __future__ import annotations

from io import BytesIO

from django.urls import reverse
from openpyxl import load_workbook

from apps.core.import_testing import build_workbook, upload

from . import importers
from .models import Class, Level, Subject
from .tests import AcademicsTestCase


def class_row(**overrides) -> dict:
    row = {"name": "Grade 1", "stream": "", "level": "", "year_in_level": ""}
    row.update(overrides)
    return row


def subject_row(**overrides) -> dict:
    row = {"name": "Further Mathematics", "code": "FMT", "classes": ""}
    row.update(overrides)
    return row


def class_xlsx(rows, **kwargs) -> bytes:
    return build_workbook(importers.CLASS_SHEET, rows, **kwargs)


def subject_xlsx(rows, **kwargs) -> bytes:
    return build_workbook(importers.SUBJECT_SHEET, rows, **kwargs)


class ImportTestCase(AcademicsTestCase):
    """Signed in as the North principal, whose branch needs no choosing."""

    def setUp(self):
        self.client.force_login(self.north_principal)

    def post_file(self, url, content, **extra):
        return self.client.post(
            reverse(url), {"upload": upload(content), **extra}
        )


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------


class ClassValidationTests(ImportTestCase):
    def validate(self, dicts, branch=None):
        rows = [
            importers.SourceRow(number=index, values=values)
            for index, values in enumerate(dicts, start=2)
        ]
        with self.as_user(self.north_principal):
            return importers.validate_classes(rows, branch=branch or self.north)

    def test_a_clean_sheet_passes_every_row(self):
        report = self.validate([
            class_row(name="Grade 1"),
            class_row(name="Grade 2"),
        ])
        self.assertEqual((report.ready_count, report.failed_count), (2, 0))
        self.assertEqual(report.ready[0].record.branch, self.north)
        self.assertEqual(report.ready[0].record.school_id, self.alpha.pk)

    def test_validation_writes_nothing(self):
        before = Class.all_objects.count()
        self.validate([class_row(), class_row(name="Grade 2")])
        self.assertEqual(Class.all_objects.count(), before)

    def test_a_missing_name_is_refused_by_column(self):
        report = self.validate([class_row(name="")])
        self.assertEqual(report.failed_count, 1)
        self.assertEqual(report.failed[0].errors[0].column, "Class")
        self.assertIn("required", report.failed[0].errors[0].message)

    # -- the band, given or inferred -----------------------------------------

    def test_a_level_is_read_off_the_class_name(self):
        """The whole point of the optional columns: a school that writes
        "Basic 7" should not have to know it is junior secondary."""
        report = self.validate([
            class_row(name="Basic 3"),
            class_row(name="Basic 7"),
            class_row(name="SSS 2"),
            class_row(name="KG 2"),
        ])
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(
            [row.record.level for row in report.ready],
            [Level.PRIMARY, Level.JUNIOR_SECONDARY, Level.SENIOR_SECONDARY,
             Level.NURSERY],
        )

    def test_a_year_is_read_off_the_class_name(self):
        report = self.validate([class_row(name="Grade 4")])
        self.assertEqual(report.ready[0].record.year_in_level, 4)

    def test_a_name_with_no_band_in_it_is_refused_rather_than_guessed(self):
        """The rule this import turns on. Filing "Alpha Class" under Nursery
        would be a guess the school never sees and cannot correct."""
        report = self.validate([class_row(name="Alpha Class")])
        self.assertEqual(report.failed_count, 1)
        message = report.failed[0].summary
        self.assertIn("Level", message)
        # The four bands are named, so the fix is on screen.
        self.assertIn("Senior Secondary", message)

    def test_a_given_level_wins_over_the_name(self):
        report = self.validate([
            class_row(name="Grade 10", level="Senior Secondary")
        ])
        self.assertEqual(report.ready[0].record.level, Level.SENIOR_SECONDARY)

    def test_a_level_may_be_spelled_the_way_schools_spell_it(self):
        for spelling in ("JSS", "junior", "Junior Secondary", "js"):
            report = self.validate([class_row(name="Alpha", level=spelling)])
            self.assertEqual(report.failed_count, 0, spelling)
            self.assertEqual(
                report.ready[0].record.level, Level.JUNIOR_SECONDARY, spelling
            )

    def test_an_unreadable_level_names_the_ones_that_work(self):
        report = self.validate([class_row(name="Grade 1", level="Middle School")])
        self.assertEqual(report.failed_count, 1)
        self.assertIn("Nursery", report.failed[0].summary)

    def test_a_year_outside_the_range_is_refused(self):
        report = self.validate([class_row(name="Grade 1", year_in_level="2025")])
        self.assertEqual(report.failed_count, 1)
        self.assertIn("Year", report.failed[0].summary)

    def test_a_year_typed_as_a_number_survives_excel(self):
        """Excel hands back 4.0 for a cell typed as 4; it is still year four."""
        report = self.validate([class_row(name="Grade 1", year_in_level="4.0")])
        self.assertEqual(report.ready[0].record.year_in_level, 4)

    # -- duplicates ----------------------------------------------------------

    def test_a_class_already_at_the_branch_is_refused(self):
        report = self.validate([class_row(name="JSS 1")])
        self.assertEqual(report.failed_count, 1)
        self.assertIn("already a class at North", report.failed[0].summary)

    def test_case_and_spacing_do_not_hide_a_duplicate(self):
        report = self.validate([class_row(name="jss  1")])
        self.assertEqual(report.failed_count, 1)
        self.assertIn("already a class", report.failed[0].summary)

    def test_the_same_class_at_another_branch_does_not_count(self):
        """South runs a JSS 1 too; importing one at North is not a clash."""
        report = self.validate([class_row(name="Primary 1")], branch=self.south)
        self.assertEqual(report.failed_count, 0)

    def test_the_second_mention_in_a_file_is_the_one_refused(self):
        report = self.validate([
            class_row(name="Grade 1"),
            class_row(name="Grade 1"),
        ])
        self.assertEqual((report.ready_count, report.failed_count), (1, 1))
        self.assertEqual(report.failed[0].number, 3)
        self.assertIn("row 2 of this file", report.failed[0].summary)

    def test_two_arms_of_one_year_are_not_duplicates(self):
        report = self.validate([
            class_row(name="SSS 1", stream="Arts"),
            class_row(name="SSS 1", stream="Science"),
        ])
        self.assertEqual((report.ready_count, report.failed_count), (2, 0))


class ClassImportScreenTests(ImportTestCase):
    def test_the_template_downloads_as_a_workbook(self):
        response = self.client.get(reverse("academics:class_import_template"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])
        self.assertTrue(response.content.startswith(b"PK"))

    def test_the_template_names_the_bands_it_will_accept(self):
        response = self.client.get(reverse("academics:class_import_template"))
        page = load_workbook(BytesIO(response.content))["Classes"]
        headers = [cell.value for cell in page[1]]
        self.assertIn("Class *", headers)      # required, marked
        self.assertIn("Level", headers)        # optional, not marked
        guidance = " ".join(str(c.value) for c in page[2])
        self.assertIn("Junior Secondary", guidance)

    def test_the_template_reads_back_through_our_own_reader(self):
        """Our file has to survive our reader: the guidance row is skipped
        rather than imported as a class called EXAMPLE."""
        response = self.client.get(reverse("academics:class_import_template"))
        from apps.core.spreadsheets import WorkbookError, read_rows

        with self.assertRaises(WorkbookError) as caught:
            read_rows(BytesIO(response.content), importers.CLASS_SHEET)
        self.assertIn("no classes", str(caught.exception))

    def test_a_clean_file_imports_every_row(self):
        content = class_xlsx([
            class_row(name="Grade 1"),
            class_row(name="Grade 2"),
            class_row(name="Grade 3"),
        ], guidance_row=True)

        self.assertRedirects(
            self.post_file("academics:class_import", content),
            reverse("academics:class_import_review"),
        )
        report = self.client.get(
            reverse("academics:class_import_review")
        ).context["report"]
        self.assertEqual((report.total, report.ready_count), (3, 3))
        self.assertFalse(Class.all_objects.filter(name="Grade 1").exists())

        response = self.client.post(
            reverse("academics:class_import_review"), {"action": "import"}
        )
        self.assertRedirects(response, reverse("academics:class_list"))
        created = Class.all_objects.filter(name__startswith="Grade")
        self.assertEqual(created.count(), 3)
        self.assertEqual({k.branch_id for k in created}, {self.north.pk})

    def test_the_good_rows_still_go_in_when_one_fails(self):
        content = class_xlsx([
            class_row(name="Grade 1"),
            class_row(name="Alpha Class"),   # no band, refused
            class_row(name="Grade 3"),
        ])
        self.post_file("academics:class_import", content)
        report = self.client.get(
            reverse("academics:class_import_review")
        ).context["report"]
        self.assertEqual((report.ready_count, report.failed_count), (2, 1))

        self.client.post(
            reverse("academics:class_import_review"), {"action": "import"}
        )
        self.assertEqual(Class.all_objects.filter(name="Grade 1").count(), 1)
        self.assertEqual(Class.all_objects.filter(name="Alpha Class").count(), 0)

    def test_a_file_of_nothing_but_bad_rows_writes_nothing(self):
        before = Class.all_objects.count()
        self.post_file("academics:class_import", class_xlsx([class_row(name="JSS 1")]))
        response = self.client.post(
            reverse("academics:class_import_review"), {"action": "import"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Class.all_objects.count(), before)

    def test_discarding_leaves_nothing_behind(self):
        self.post_file("academics:class_import", class_xlsx([class_row()]))
        response = self.client.post(
            reverse("academics:class_import_review"), {"action": "discard"}
        )
        self.assertRedirects(response, reverse("academics:class_import"))
        self.assertNotIn("academics.class_import", self.client.session)

    def test_the_file_is_rechecked_before_it_is_written(self):
        """The name was free at upload and taken by the time they pressed the
        button."""
        self.post_file("academics:class_import", class_xlsx([class_row(name="Grade 1")]))
        self.assertEqual(
            self.client.get(
                reverse("academics:class_import_review")
            ).context["report"].ready_count,
            1,
        )
        Class.all_objects.create(
            branch=self.north, name="Grade 1", level=Level.PRIMARY, year_in_level=1
        )
        response = self.client.post(
            reverse("academics:class_import_review"), {"action": "import"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Class.all_objects.filter(name="Grade 1").count(), 1)

    def test_the_class_list_offers_both_routes_when_there_are_none(self):
        """The choice the client asked for: the standard set, or your own."""
        with self.as_user(self.beta_owner):
            self.client.force_login(self.beta_owner)
            Class.all_objects.filter(branch=self.beta_main).delete()
            response = self.client.get(reverse("academics:class_list"))
        self.assertContains(response, reverse("academics:class_bulk_create"))
        self.assertContains(response, reverse("academics:class_import"))
        self.assertContains(response, "standard Nigerian set")


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------


class SubjectValidationTests(ImportTestCase):
    def validate(self, dicts, branch=None):
        rows = [
            importers.SourceRow(number=index, values=values)
            for index, values in enumerate(dicts, start=2)
        ]
        with self.as_user(self.north_principal):
            return importers.validate_subjects(rows, branch=branch or self.north)

    def test_a_clean_sheet_passes_every_row(self):
        report = self.validate([
            subject_row(name="Further Mathematics"),
            subject_row(name="Technical Drawing", code="TDR"),
        ])
        self.assertEqual((report.ready_count, report.failed_count), (2, 0))
        self.assertEqual(report.ready[0].record.branch, self.north)

    def test_a_blank_taught_in_is_allowed(self):
        """A school importing its subject list before its classes has not made
        a mistake -- the subjects screen already counts the unassigned ones."""
        report = self.validate([subject_row(classes="")])
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(report.ready[0].extra["classes"], [])

    def test_a_class_name_in_taught_in_is_resolved(self):
        report = self.validate([subject_row(classes="JSS 1")])
        self.assertEqual(
            report.ready[0].extra["classes"], [self.north_jss1]
        )

    def test_a_level_name_resolves_to_that_levels_classes(self):
        Class.all_objects.create(
            branch=self.north, name="JSS 2", level=Level.JUNIOR_SECONDARY,
            year_in_level=2,
        )
        report = self.validate([subject_row(classes="Junior Secondary")])
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(
            {k.name for k in report.ready[0].extra["classes"]}, {"JSS 1", "JSS 2"}
        )

    def test_all_means_every_class_at_the_campus(self):
        Class.all_objects.create(
            branch=self.north, name="Primary 1", level=Level.PRIMARY, year_in_level=1
        )
        report = self.validate([subject_row(classes="All")])
        self.assertEqual(
            {k.name for k in report.ready[0].extra["classes"]},
            {"JSS 1", "Primary 1"},
        )

    def test_several_names_in_one_cell_are_split(self):
        Class.all_objects.create(
            branch=self.north, name="Primary 6", level=Level.PRIMARY, year_in_level=6
        )
        report = self.validate([subject_row(classes="JSS 1; Primary 6")])
        self.assertEqual(len(report.ready[0].extra["classes"]), 2)

    def test_a_comma_works_as_well_as_a_semicolon(self):
        Class.all_objects.create(
            branch=self.north, name="Primary 6", level=Level.PRIMARY, year_in_level=6
        )
        report = self.validate([subject_row(classes="JSS 1, Primary 6")])
        self.assertEqual(len(report.ready[0].extra["classes"]), 2)

    def test_naming_a_level_and_one_of_its_classes_is_not_a_duplicate(self):
        report = self.validate([subject_row(classes="Junior Secondary; JSS 1")])
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(report.ready[0].extra["classes"], [self.north_jss1])

    def test_every_unresolved_name_is_reported_not_just_the_first(self):
        """A school sent back to fix one typo per upload is the frustration
        this screen exists to remove."""
        report = self.validate([subject_row(classes="JSS 1; Nowhere; Elsewhere")])
        self.assertEqual(report.failed_count, 1)
        summary = report.failed[0].summary
        self.assertIn("Nowhere", summary)
        self.assertIn("Elsewhere", summary)

    def test_a_class_name_resolves_against_the_chosen_branch_only(self):
        """Three branches run a "JSS 1". The one a row gets is the one belonging
        to the campus the import is for, and the scope it is read under."""
        rows = [
            importers.SourceRow(number=2, values=subject_row(classes="JSS 1"))
        ]
        with self.as_user(self.beta_owner):
            report = importers.validate_subjects(rows, branch=self.beta_main)
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(report.ready[0].extra["classes"], [self.beta_jss1])

    def test_a_subject_already_at_the_branch_is_refused(self):
        report = self.validate([subject_row(name="Mathematics")])
        self.assertEqual(report.failed_count, 1)
        self.assertIn("already a subject at North", report.failed[0].summary)

    def test_the_second_mention_in_a_file_is_the_one_refused(self):
        report = self.validate([
            subject_row(name="Civic Education"),
            subject_row(name="civic education"),
        ])
        self.assertEqual((report.ready_count, report.failed_count), (1, 1))
        self.assertEqual(report.failed[0].number, 3)


class SubjectImportScreenTests(ImportTestCase):
    def test_the_template_lists_the_campuss_classes_and_the_bands(self):
        response = self.client.get(reverse("academics:subject_import_template"))
        book = load_workbook(BytesIO(response.content))
        self.assertIn("Classes", book.sheetnames)
        listed = {
            row[0] for row in book["Classes"].iter_rows(min_row=2, values_only=True)
        }
        self.assertIn("JSS 1", listed)          # North's own
        self.assertIn("Primary", listed)        # a band is a valid answer too

    def test_a_clean_file_imports_and_attaches_its_classes(self):
        content = subject_xlsx([
            subject_row(name="Further Mathematics", classes="JSS 1"),
            subject_row(name="Technical Drawing", code="TDR", classes=""),
        ], guidance_row=True)
        self.post_file("academics:subject_import", content)
        self.client.post(
            reverse("academics:subject_import_review"), {"action": "import"}
        )

        further = Subject.all_objects.get(name="Further Mathematics")
        self.assertEqual(further.branch, self.north)
        self.assertEqual(list(further.classes.all()), [self.north_jss1])
        drawing = Subject.all_objects.get(name="Technical Drawing")
        self.assertEqual(drawing.classes.count(), 0)

    def test_a_failed_row_does_not_stop_the_others(self):
        content = subject_xlsx([
            subject_row(name="Further Mathematics"),
            subject_row(name="Mathematics"),       # already taught at North
            subject_row(name="Technical Drawing"),
        ])
        self.post_file("academics:subject_import", content)
        self.client.post(
            reverse("academics:subject_import_review"), {"action": "import"}
        )
        self.assertTrue(Subject.all_objects.filter(name="Further Mathematics").exists())
        self.assertEqual(
            Subject.all_objects.filter(name="Mathematics", branch=self.north).count(), 1
        )


# ---------------------------------------------------------------------------
# Permissions and tenancy
# ---------------------------------------------------------------------------


class ImportPermissionTests(ImportTestCase):
    IMPORT_URLS = (
        "academics:class_import",
        "academics:class_import_template",
        "academics:class_import_review",
        "academics:subject_import",
        "academics:subject_import_template",
        "academics:subject_import_review",
    )

    def test_a_bursar_is_refused_every_import_screen(self):
        self.client.force_login(self.north_bursar)
        for name in self.IMPORT_URLS:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_a_bursar_cannot_post_a_file_either(self):
        self.client.force_login(self.north_bursar)
        before = Class.all_objects.count()
        response = self.post_file("academics:class_import", class_xlsx([class_row()]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Class.all_objects.count(), before)

    def test_signed_out_visitors_are_sent_to_the_login_page(self):
        self.client.logout()
        response = self.client.get(reverse("academics:class_import"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_principal_is_not_asked_which_campus(self):
        response = self.client.get(reverse("academics:class_import"))
        self.assertNotIn("branch", response.context["form"].fields)

    def test_a_principal_cannot_import_into_another_branch(self):
        """The field does not exist for them, and a forged one is ignored."""
        self.post_file(
            "academics:class_import",
            class_xlsx([class_row(name="Grade 9")]),
            branch=self.south.pk,
        )
        self.client.post(
            reverse("academics:class_import_review"), {"action": "import"}
        )
        klass = Class.all_objects.get(name="Grade 9")
        self.assertEqual(klass.branch, self.north)

    def test_a_school_owner_picks_the_campus_and_it_is_honoured(self):
        self.client.force_login(self.alpha_owner)
        response = self.client.get(reverse("academics:class_import"))
        self.assertIn("branch", response.context["form"].fields)

        self.post_file(
            "academics:class_import",
            class_xlsx([class_row(name="Grade 9")]),
            branch=self.south.pk,
        )
        self.client.post(
            reverse("academics:class_import_review"), {"action": "import"}
        )
        self.assertEqual(Class.all_objects.get(name="Grade 9").branch, self.south)

    def test_one_schools_import_cannot_touch_another(self):
        self.client.force_login(self.beta_owner)
        self.post_file(
            "academics:subject_import",
            subject_xlsx([subject_row(name="Mathematics")]),
        )
        report = self.client.get(
            reverse("academics:subject_import_review")
        ).context["report"]
        # Beta has no Mathematics of its own, so Alpha's is invisible and the
        # row passes.
        self.assertEqual(report.ready_count, 1)
        self.client.post(
            reverse("academics:subject_import_review"), {"action": "import"}
        )
        self.assertEqual(
            Subject.all_objects.filter(name="Mathematics").count(), 3
        )
