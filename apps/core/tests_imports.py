"""Tests for the machinery all four bulk imports share.

Four entities now run on one set of rails, so a bug here is a bug four times.
These tests cover the parts that have no model behind them -- cell parsing,
header matching, the guidance row, the report's arithmetic -- plus two
structural checks over every registered sheet, because both of them have already
caught something:

* a column key that is not a field on the model it feeds silently stops
  ``Judgement.ask_the_model`` excluding it, and the user is told about the same
  empty cell twice in two different voices;
* a guidance column that a school might legitimately leave blank means the
  template's own example row is not recognised on the way back in, and
  "EXAMPLE" is imported as a class.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from django.test import SimpleTestCase

from apps.academics import importers as academics_importers
from apps.fees import importers as fees_importers
from apps.students import importer as students_importer

from .import_testing import build_workbook
from .imports import (
    Column,
    ImportReport,
    Judgement,
    Sheet,
    SourceRow,
    normalise_header,
    parse_amount,
    parse_date,
    parse_list,
)
from .spreadsheets import WorkbookError, build_template, read_rows

#: Every sheet in the application, with the model each row of it builds. The
#: structural tests below walk this, so adding a fifth import without adding it
#: here is the one way to escape them -- which is why it lives next to them
#: rather than in a settings file.
ALL_SHEETS = (
    ("students", students_importer.SHEET, "apps.students.models.Student"),
    ("classes", academics_importers.CLASS_SHEET, "apps.academics.models.Class"),
    ("subjects", academics_importers.SUBJECT_SHEET, "apps.academics.models.Subject"),
    ("fees", fees_importers.SHEET, "apps.fees.models.FeeComponent"),
)

#: Column keys that are deliberately not model fields: they are parsed into
#: something else before the record is built, and are excluded from
#: ``ask_the_model`` by name in their importer.
NOT_MODEL_FIELDS = {
    # The sheet says "Primary 1"; the importer resolves it to a Class and the
    # fee row carries it on ``extra`` rather than on the component.
    ("fees", "school_class"),
    # Several class names in one cell, resolved to a list for the m2m.
    ("subjects", "classes"),
}


def _model(path: str):
    from importlib import import_module

    module, name = path.rsplit(".", 1)
    return getattr(import_module(module), name)


class EverySheetTests(SimpleTestCase):
    """Structural rules that hold for every import, present and future."""

    def test_every_column_key_is_a_field_on_the_model_it_feeds(self):
        """The bug this exists for: the fee sheet keyed its item column
        ``component`` while the model field is ``name``, so refusing an empty
        cell no longer stopped the model complaining about the same cell."""
        for label, sheet, path in ALL_SHEETS:
            fields = {f.name for f in _model(path)._meta.get_fields()}
            for column in sheet.columns:
                if (label, column.key) in NOT_MODEL_FIELDS:
                    continue
                self.assertIn(
                    column.key, fields,
                    f"{label}: column {column.key!r} is not a field on {path} "
                    f"-- either rename it or list it in NOT_MODEL_FIELDS",
                )

    def test_every_guidance_column_is_a_required_one(self):
        """A school may leave an optional column blank, and then the template's
        own example row is not recognised and "EXAMPLE" is imported."""
        for label, sheet, _ in ALL_SHEETS:
            self.assertIn(
                sheet.guidance_column, sheet.required_keys,
                f"{label}: the guidance marker sits in an optional column",
            )

    def test_every_sheet_has_a_distinct_download_filename(self):
        names = [sheet.filename for _, sheet, _ in ALL_SHEETS]
        self.assertEqual(len(names), len(set(names)))

    def test_every_label_key_names_a_real_column(self):
        """``label`` is how a failed row is identified in the report. A typo
        here makes every failure read "(blank row)"."""
        for label, sheet, _ in ALL_SHEETS:
            for key in sheet.label_keys:
                self.assertIn(key, sheet.by_key, f"{label}: {key!r}")

    def test_every_template_survives_our_own_reader(self):
        """Our file has to read back: the guidance row is skipped rather than
        imported, which means the file parses and then reports no data rows."""
        for label, sheet, _ in ALL_SHEETS:
            content = build_template(sheet)
            with self.assertRaises(WorkbookError, msg=label) as caught:
                read_rows(BytesIO(content), sheet)
            self.assertIn(sheet.noun_plural, str(caught.exception), label)


class AmountParsingTests(SimpleTestCase):
    def test_the_shapes_excel_and_a_typist_produce(self):
        for text, expected in (
            ("45000", "45000"),
            ("45,000", "45000"),
            ("₦45,000", "45000"),
            ("NGN 45000", "45000"),
            ("45 000", "45000"),
            (" 45,000 ", "45000"),   # the space Excel inserts
            ("45000.50", "45000.50"),
            ("0", "0"),
        ):
            self.assertEqual(parse_amount(text), Decimal(expected), text)

    def test_what_is_not_money(self):
        for text in ("", "   ", "free", "N/A", "-", "see note", "."):
            self.assertIsNone(parse_amount(text), text)

    def test_accounting_parentheses_come_back_negative(self):
        """So the caller refuses them, rather than reading a credit as a charge."""
        self.assertEqual(parse_amount("(5,000)"), Decimal("-5000"))


class ListParsingTests(SimpleTestCase):
    def test_both_separators_a_school_would_reach_for(self):
        self.assertEqual(parse_list("JSS 1; JSS 2"), ["JSS 1", "JSS 2"])
        self.assertEqual(parse_list("JSS 1, JSS 2"), ["JSS 1", "JSS 2"])
        self.assertEqual(parse_list("JSS 1 / JSS 2"), ["JSS 1", "JSS 2"])

    def test_empty_pieces_are_dropped(self):
        self.assertEqual(parse_list("JSS 1;; ; JSS 2;"), ["JSS 1", "JSS 2"])

    def test_nothing_is_an_empty_list_not_a_blank_entry(self):
        self.assertEqual(parse_list(""), [])
        self.assertEqual(parse_list("  "), [])


class DateParsingTests(SimpleTestCase):
    def test_day_first_is_the_default_reading(self):
        """"04/05/2019" is the fourth of May on this continent."""
        self.assertEqual(parse_date("04/05/2019").month, 5)

    def test_iso_is_read_whichever_way_you_look_at_it(self):
        parsed = parse_date("2019-04-12")
        self.assertEqual((parsed.year, parsed.month, parsed.day), (2019, 4, 12))

    def test_what_is_not_a_date(self):
        for text in ("", "soon", "12/13/2019"):
            self.assertIsNone(parse_date(text), text)


class HeaderMatchingTests(SimpleTestCase):
    def test_punctuation_spacing_and_case_are_all_noise(self):
        self.assertEqual(normalise_header("  Parent / Guardian Phone "),
                         normalise_header("parent guardian phone"))
        self.assertEqual(normalise_header("Year  In	Level"),
                         normalise_header("year in level"))

    def test_punctuation_separates_rather_than_vanishing(self):
        """So an acronym is not silently the same word as its letters, and a
        dotted spelling has to be listed as its own alias."""
        self.assertEqual(normalise_header("D.O.B."), "d o b")
        self.assertNotEqual(normalise_header("D.O.B."), normalise_header("dob"))

    def test_the_dotted_spelling_of_a_date_of_birth_is_still_matched(self):
        """Because the column lists it, which is the only thing that makes the
        docstring's claim true."""
        self.assertEqual(
            students_importer.SHEET.column_for_header("D.O.B."), "date_of_birth"
        )
        self.assertEqual(
            students_importer.SHEET.column_for_header("DOB"), "date_of_birth"
        )

    def test_the_required_asterisk_our_own_template_writes_is_ignored(self):
        """We get our own file back, and its headers carry the asterisk."""
        sheet = students_importer.SHEET
        self.assertEqual(
            sheet.column_for_header("Admission Number *"), "admission_number"
        )

    def test_an_alias_a_schools_own_sheet_would_use_is_matched(self):
        sheet = students_importer.SHEET
        self.assertEqual(sheet.column_for_header("Adm No"), "admission_number")
        self.assertEqual(sheet.column_for_header("Last Name"), "last_name")

    def test_a_heading_we_have_no_field_for_is_not_guessed_at(self):
        self.assertIsNone(
            students_importer.SHEET.column_for_header("House Colour")
        )


# ---------------------------------------------------------------------------
# A throwaway sheet, for the machinery's own edge cases
# ---------------------------------------------------------------------------

TOY = Sheet(
    name="Toy",
    noun="widget",
    noun_plural="widgets",
    filename="toy.xlsx",
    columns=(
        Column("code", "Code", True, "W-1", ("reference",)),
        Column("note", "Note", False, "anything"),
    ),
    header_match_threshold=1,
)


class ReaderTests(SimpleTestCase):
    def rows(self, **kwargs):
        return read_rows(BytesIO(build_workbook(TOY, **kwargs)), TOY).rows

    def test_a_title_line_above_the_headers_does_not_stop_the_read(self):
        """A school's own export usually has one."""
        rows = self.rows(
            rows=[{"code": "W-1"}], title_row="Fulfilled Academy — widgets"
        )
        self.assertEqual([r.get("code") for r in rows], ["W-1"])
        # The row number is the spreadsheet's own, so the report can send the
        # user straight to it.
        self.assertEqual(rows[0].number, 4)

    def test_reordered_columns_still_read(self):
        rows = self.rows(
            rows=[{"code": "W-1", "note": "hello"}], headers=["note", "code"]
        )
        self.assertEqual(rows[0].get("code"), "W-1")
        self.assertEqual(rows[0].get("note"), "hello")

    def test_blank_rows_are_skipped_wherever_they_are(self):
        """A spacer between sections is how people lay a sheet out by hand."""
        rows = self.rows(rows=[{"code": "W-1"}, {}, {"code": "W-2"}])
        self.assertEqual([r.get("code") for r in rows], ["W-1", "W-2"])

    def test_the_guidance_row_is_skipped_not_imported(self):
        rows = self.rows(rows=[{"code": "W-1"}], guidance_row=True)
        self.assertEqual([r.get("code") for r in rows], ["W-1"])

    def test_a_missing_required_column_is_refused_by_name(self):
        with self.assertRaises(WorkbookError) as caught:
            self.rows(rows=[{"note": "hello"}], headers=["note"])
        self.assertIn("Code", str(caught.exception))

    def test_a_file_that_is_not_a_workbook_is_refused_in_words(self):
        with self.assertRaises(WorkbookError) as caught:
            read_rows(BytesIO(b"name,code\nWidget,W-1\n"), TOY)
        self.assertIn(".xlsx", str(caught.exception))

    def test_an_unknown_heading_is_reported_rather_than_refused(self):
        """A school's own sheet carries columns we do not want."""
        parsed = read_rows(
            BytesIO(build_workbook(
                TOY, rows=[{"code": "W-1"}], headers=["code", "house"]
            )),
            TOY,
        )
        self.assertEqual(parsed.unknown_columns, ["house"])

    def test_an_absent_optional_column_is_reported_as_left_blank(self):
        parsed = read_rows(
            BytesIO(build_workbook(TOY, rows=[{"code": "W-1"}], headers=["code"])),
            TOY,
        )
        self.assertEqual(parsed.absent_columns, ["Note"])

    def test_our_own_reference_tab_does_not_get_read_as_the_data(self):
        """The reader prefers the sheet the contract names. Without that, a
        school who dragged the Classes tab to the front would have its class
        list read as widgets."""
        content = build_template(TOY)
        # Rebuild with the reference tab first, the way a reordered file arrives.
        from openpyxl import load_workbook

        book = load_workbook(BytesIO(content))
        page = book[TOY.name]
        page.append(["W-9", "real data"])
        extra = book.create_sheet("Reference", 0)
        extra["A1"] = "Something else entirely"
        buffer = BytesIO()
        book.save(buffer)

        rows = read_rows(BytesIO(buffer.getvalue()), TOY).rows
        self.assertEqual([r.get("code") for r in rows], ["W-9"])


class ReportTests(SimpleTestCase):
    def judge(self, values, *, refuse=None):
        source = SourceRow(number=7, values=values)
        judgement = Judgement(TOY)
        judgement.require(source)
        if refuse:
            judgement.refuse(*refuse)
        return judgement.verdict(source, object())

    def test_a_row_that_passed_carries_the_record(self):
        result = self.judge({"code": "W-1", "note": ""})
        self.assertTrue(result.is_valid)
        self.assertIsNotNone(result.record)

    def test_a_row_that_failed_carries_nothing_to_write(self):
        """So commit() has no decision left to make."""
        result = self.judge({"code": "", "note": ""})
        self.assertFalse(result.is_valid)
        self.assertIsNone(result.record)

    def test_a_refusal_names_the_column_as_the_sheet_names_it(self):
        result = self.judge({"code": "W-1"}, refuse=("code", "is taken."))
        self.assertEqual(result.errors[0].column, "Code")

    def test_the_summary_reads_as_a_sentence_with_the_row_number(self):
        result = self.judge({"code": "W-1"}, refuse=("code", "is taken."))
        self.assertEqual(result.summary, "Row 7: Code is taken.")

    def test_a_label_falls_back_to_something_sayable(self):
        self.assertEqual(self.judge({"code": "", "note": ""}).label, "(blank row)")
        self.assertEqual(self.judge({"code": "W-1"}).label, "W-1")

    def test_the_report_counts_both_sides(self):
        report = ImportReport(sheet=TOY)
        report.rows = [
            self.judge({"code": "W-1"}),
            self.judge({"code": ""}),
            self.judge({"code": "W-3"}),
        ]
        self.assertEqual((report.total, report.ready_count, report.failed_count),
                         (3, 2, 1))
        self.assertTrue(report.has_anything_to_import)

    def test_a_report_with_nothing_ready_says_so(self):
        report = ImportReport(sheet=TOY)
        report.rows = [self.judge({"code": ""})]
        self.assertFalse(report.has_anything_to_import)


class SessionRoundTripTests(SimpleTestCase):
    def test_a_parsed_row_survives_the_session(self):
        """The rows wait in the session between the report and the confirmation,
        so they have to be plain JSON and come back identical."""
        original = SourceRow(number=14, values={"code": "W-1", "note": "hello"})
        payload = original.as_dict()
        self.assertEqual(SourceRow.from_dict(payload), original)
