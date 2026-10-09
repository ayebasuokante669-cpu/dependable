"""Tests for the fee structure import.

Money, so the rules are stricter than the other three imports and the ones worth
naming are:

* an amount that cannot be read as money is **refused**, never coerced to zero;
* a negative amount is refused, because a fee is what the parent owes;
* a charge the class already has for that term is refused rather than doubled;
* a structure is created only when something is going into it, so a file whose
  Primary 1 rows all failed leaves no empty structure reading ₦0 behind -- the
  fee list would show that as "priced" and bill nobody;
* positions continue from what the structure already holds, so an import adds to
  the bottom of a hand-built list rather than into the middle of it.

The fixture is :class:`apps.fees.tests.FeesTestCase`: North runs Primary 1,
Primary 2 and KG 1, Primary 1 is already priced at ₦78,000 for the current term,
and South runs a Primary 1 of its own.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from django.urls import reverse
from openpyxl import load_workbook

from apps.core.import_testing import build_workbook, upload

from . import importers
from .models import FeeComponent, FeeStructure
from .tests import FeesTestCase


def fee_row(**overrides) -> dict:
    row = {"school_class": "Primary 2", "name": "Tuition", "amount": "45000"}
    row.update(overrides)
    return row


def fee_xlsx(rows, **kwargs) -> bytes:
    return build_workbook(importers.SHEET, rows, **kwargs)


class FeeImportTestCase(FeesTestCase):
    """Signed in as the school owner, pricing North's term unless told otherwise.

    The owner rather than a principal because importing fees is changing the fee
    structure, which is the owner's alone. The owner sees every campus's terms,
    so the term is chosen on every upload and every template download.
    """

    def setUp(self):
        self.client.force_login(self.alpha_owner)

    def validate(self, dicts, term=None, user=None):
        user = user or self.north_principal
        rows = [
            importers.SourceRow(number=index, values=values)
            for index, values in enumerate(dicts, start=2)
        ]
        with self.as_user(user):
            return importers.validate(rows, term=term or self.north_term)

    def post_file(self, content, **extra):
        extra.setdefault("term", self.north_term.pk)
        return self.client.post(
            reverse("fees:structure_import"), {"upload": upload(content), **extra}
        )

    def review(self):
        return self.client.get(reverse("fees:structure_import_review"))

    def confirm(self):
        return self.client.post(
            reverse("fees:structure_import_review"), {"action": "import"}
        )


class AmountTests(FeeImportTestCase):
    """What Excel does to a column of money, undone."""

    def test_the_shapes_a_school_actually_types(self):
        for text, expected in (
            ("45000", 45000),
            ("45,000", 45000),
            ("₦45,000", 45000),
            ("NGN 45000", 45000),
            ("45000.50", Decimal("45000.50")),
            ("45 000", 45000),
        ):
            report = self.validate([fee_row(amount=text)])
            self.assertEqual(report.failed_count, 0, text)
            self.assertEqual(report.ready[0].record.amount, Decimal(expected), text)

    def test_words_are_refused_rather_than_read_as_zero(self):
        report = self.validate([fee_row(amount="free")])
        self.assertEqual(report.failed_count, 1)
        self.assertIn("Amount", report.failed[0].summary)
        self.assertIn("could not be read", report.failed[0].summary)

    def test_a_negative_amount_is_refused(self):
        """A fee is what the parent owes. A credit is a payment, not a fee."""
        report = self.validate([fee_row(amount="-5000")])
        self.assertEqual(report.failed_count, 1)
        self.assertIn("negative", report.failed[0].summary)

    def test_accounting_parentheses_are_read_as_negative_and_refused(self):
        """(5,000) means minus five thousand in a ledger. Dropping the brackets
        would turn a credit into a charge."""
        report = self.validate([fee_row(amount="(5,000)")])
        self.assertEqual(report.failed_count, 1)
        self.assertIn("negative", report.failed[0].summary)

    def test_an_absurd_amount_is_refused_with_a_hint_about_zeros(self):
        report = self.validate([fee_row(amount="4500000000")])
        self.assertEqual(report.failed_count, 1)
        self.assertIn("extra zero", report.failed[0].summary)

    def test_zero_is_allowed(self):
        """A school that lists a chargeable item at nothing this term is
        describing its own fees, not making a mistake."""
        report = self.validate([fee_row(amount="0")])
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(report.ready[0].record.amount, Decimal("0"))


class FeeValidationTests(FeeImportTestCase):
    def test_a_clean_sheet_passes_every_row(self):
        report = self.validate([
            fee_row(school_class="Primary 2", name="Tuition", amount="45000"),
            fee_row(school_class="Primary 2", name="Books", amount="12000"),
            fee_row(school_class="KG 1", name="Tuition", amount="30000"),
        ])
        self.assertEqual((report.ready_count, report.failed_count), (3, 0))

    def test_validation_writes_nothing(self):
        before = FeeComponent.all_objects.count()
        self.validate([fee_row(), fee_row(name="Books")])
        self.assertEqual(FeeComponent.all_objects.count(), before)

    def test_every_column_is_required(self):
        report = self.validate([
            {"school_class": "", "name": "", "amount": ""}
        ])
        columns = {error.column for error in report.failed[0].errors}
        self.assertEqual(columns, {"Class", "Fee Item", "Amount"})

    def test_an_unknown_class_is_refused_with_the_campus_named(self):
        report = self.validate([fee_row(school_class="Primary 9")])
        self.assertEqual(report.failed_count, 1)
        self.assertIn("not a class at North", report.failed[0].summary)

    def test_a_class_at_another_campus_is_not_priceable_under_this_term(self):
        """A term settles the campus, so the classes a row may name are that
        campus's. South's own class is simply unknown here."""
        from apps.academics.models import Class, Level

        Class.all_objects.create(
            branch=self.south, name="Primary 7", level=Level.PRIMARY, year_in_level=7
        )
        report = self.validate([fee_row(school_class="Primary 7")])
        self.assertEqual(report.failed_count, 1)
        self.assertIn("not a class at North", report.failed[0].summary)

    def test_a_name_both_campuses_use_resolves_to_the_terms_own(self):
        report = self.validate([fee_row(school_class="Primary 1", name="PTA")])
        self.assertEqual(report.ready[0].extra["school_class"], self.north_p1)

    def test_an_item_the_class_already_has_is_refused_not_doubled(self):
        report = self.validate([
            fee_row(school_class="Primary 1", name="Textbooks", amount="60000")
        ])
        self.assertEqual(report.failed_count, 1)
        summary = report.failed[0].summary
        self.assertIn("already charged", summary)
        self.assertIn("50,000", summary)   # what it is currently charged

    def test_case_does_not_hide_a_charge_the_class_already_has(self):
        report = self.validate([
            fee_row(school_class="Primary 1", name="textbooks")
        ])
        self.assertEqual(report.failed_count, 1)
        self.assertIn("already charged", report.failed[0].summary)

    def test_the_same_item_twice_in_one_file_refuses_the_second(self):
        report = self.validate([
            fee_row(school_class="Primary 2", name="Tuition", amount="45000"),
            fee_row(school_class="Primary 2", name="Tuition", amount="46000"),
        ])
        self.assertEqual((report.ready_count, report.failed_count), (1, 1))
        self.assertEqual(report.failed[0].number, 3)
        self.assertIn("row 2 of this file", report.failed[0].summary)

    def test_the_same_item_for_two_classes_is_not_a_duplicate(self):
        report = self.validate([
            fee_row(school_class="Primary 2", name="Tuition"),
            fee_row(school_class="KG 1", name="Tuition"),
        ])
        self.assertEqual(report.failed_count, 0)

    def test_the_report_groups_by_class_with_a_subtotal(self):
        report = self.validate([
            fee_row(school_class="Primary 2", name="Tuition", amount="45000"),
            fee_row(school_class="KG 1", name="Tuition", amount="30000"),
            fee_row(school_class="Primary 2", name="Books", amount="12000"),
        ])
        groups = importers.grouped_ready(report)
        # Admission order, not sheet order: KG before Primary.
        self.assertEqual(
            [g["school_class"].name for g in groups], ["KG 1", "Primary 2"]
        )
        self.assertEqual(
            [g["total"] for g in groups], [Decimal("30000"), Decimal("57000")]
        )


class FeeCommitTests(FeeImportTestCase):
    def test_a_structure_is_created_for_a_class_that_had_none(self):
        self.post_file(fee_xlsx([
            fee_row(school_class="Primary 2", name="Tuition", amount="45000"),
            fee_row(school_class="Primary 2", name="Books", amount="12000"),
        ]))
        self.confirm()
        structure = FeeStructure.all_objects.get(
            school_class=self.north_p2, term=self.north_term
        )
        self.assertEqual(structure.total, Decimal("57000"))
        self.assertEqual(structure.branch, self.north)
        self.assertEqual(structure.school_id, self.alpha.pk)

    def test_items_are_added_to_a_structure_that_already_exists(self):
        before = self.north_p1_fees.total
        self.post_file(fee_xlsx([
            fee_row(school_class="Primary 1", name="PTA Levy", amount="5000")
        ]))
        self.confirm()
        self.north_p1_fees.refresh_from_db()
        self.assertEqual(self.north_p1_fees.total, before + Decimal("5000"))

    def test_an_added_item_lands_at_the_bottom_of_the_existing_list(self):
        self.post_file(fee_xlsx([
            fee_row(school_class="Primary 1", name="PTA Levy", amount="5000")
        ]))
        self.confirm()
        added = FeeComponent.all_objects.get(
            fee_structure=self.north_p1_fees, name="PTA Levy"
        )
        others = FeeComponent.all_objects.filter(
            fee_structure=self.north_p1_fees
        ).exclude(pk=added.pk)
        self.assertGreater(added.position, max(c.position for c in others))

    def test_a_class_whose_only_row_failed_gets_no_empty_structure(self):
        """The bug this guards: an empty structure reads as ₦0, and the fee list
        shows a priced class that bills nobody."""
        self.post_file(fee_xlsx([
            fee_row(school_class="Primary 2", name="Tuition", amount="45000"),
            fee_row(school_class="KG 1", name="Tuition", amount="not a number"),
        ]))
        self.confirm()
        self.assertTrue(
            FeeStructure.all_objects.filter(
                school_class=self.north_p2, term=self.north_term
            ).exists()
        )
        self.assertFalse(
            FeeStructure.all_objects.filter(
                school_class=self.north_kg1, term=self.north_term
            ).exists()
        )

    def test_a_partly_failed_class_is_priced_for_its_good_rows_only(self):
        self.post_file(fee_xlsx([
            fee_row(school_class="Primary 2", name="Tuition", amount="45000"),
            fee_row(school_class="Primary 2", name="Books", amount="free"),
        ]))
        self.confirm()
        structure = FeeStructure.all_objects.get(
            school_class=self.north_p2, term=self.north_term
        )
        self.assertEqual(structure.total, Decimal("45000"))
        self.assertEqual(structure.components.count(), 1)

    def test_a_file_of_nothing_but_bad_rows_writes_nothing(self):
        before = FeeComponent.all_objects.count()
        self.post_file(fee_xlsx([fee_row(amount="free")]))
        response = self.confirm()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FeeComponent.all_objects.count(), before)
        self.assertFalse(
            FeeStructure.all_objects.filter(school_class=self.north_p2).exists()
        )

    def test_the_file_is_rechecked_before_it_is_written(self):
        """The item was free at upload and charged by the time they pressed the
        button."""
        self.post_file(fee_xlsx([
            fee_row(school_class="Primary 1", name="PTA Levy", amount="5000")
        ]))
        self.assertEqual(self.review().context["report"].ready_count, 1)

        FeeComponent.all_objects.create(
            fee_structure=self.north_p1_fees, name="PTA Levy",
            amount=Decimal("4000"), position=9,
        )
        response = self.confirm()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            FeeComponent.all_objects.filter(
                fee_structure=self.north_p1_fees, name="PTA Levy"
            ).count(),
            1,
        )

    def test_a_finished_import_lands_on_the_term_it_priced(self):
        self.post_file(fee_xlsx([fee_row()]))
        response = self.confirm()
        self.assertRedirects(
            response,
            f"{reverse('fees:structure_list')}?term={self.north_term.pk}",
        )

    def test_discarding_leaves_nothing_behind(self):
        self.post_file(fee_xlsx([fee_row()]))
        response = self.client.post(
            reverse("fees:structure_import_review"), {"action": "discard"}
        )
        self.assertRedirects(response, reverse("fees:structure_import"))
        self.assertNotIn("fees.import", self.client.session)
        self.assertFalse(
            FeeStructure.all_objects.filter(school_class=self.north_p2).exists()
        )


class FeeImportScreenTests(FeeImportTestCase):
    def test_the_template_downloads_as_a_workbook(self):
        response = self.client.get(reverse("fees:structure_import_template"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])
        self.assertTrue(response.content.startswith(b"PK"))

    def test_the_template_lists_the_terms_own_campuss_classes(self):
        response = self.client.get(
            reverse("fees:structure_import_template"), {"term": self.north_term.pk}
        )
        book = load_workbook(BytesIO(response.content))
        self.assertIn("Classes", book.sheetnames)
        listed = {
            row[0] for row in book["Classes"].iter_rows(min_row=2, values_only=True)
        }
        self.assertEqual(listed, {"KG 1", "Primary 1", "Primary 2"})

    def test_the_template_reads_back_through_our_own_reader(self):
        response = self.client.get(
            reverse("fees:structure_import_template"), {"term": self.north_term.pk}
        )
        from apps.core.spreadsheets import WorkbookError, read_rows

        with self.assertRaises(WorkbookError) as caught:
            read_rows(BytesIO(response.content), importers.SHEET)
        self.assertIn("no fees", str(caught.exception))

    def test_the_review_screen_shows_each_classs_new_total(self):
        self.post_file(fee_xlsx([
            fee_row(school_class="Primary 2", name="Tuition", amount="45000"),
            fee_row(school_class="Primary 2", name="Books", amount="12000"),
        ]))
        response = self.review()
        self.assertContains(response, "Primary 2")
        self.assertContains(response, "57,000")

    def test_the_fee_list_offers_the_import(self):
        response = self.client.get(reverse("fees:structure_list"))
        self.assertContains(response, reverse("fees:structure_import"))


class FeeImportPermissionTests(FeeImportTestCase):
    IMPORT_URLS = (
        "fees:structure_import",
        "fees:structure_import_template",
        "fees:structure_import_review",
    )

    def test_a_bursar_is_refused_every_import_screen(self):
        self.client.force_login(self.north_bursar)
        for name in self.IMPORT_URLS:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_a_bursar_cannot_post_a_file_either(self):
        self.client.force_login(self.north_bursar)
        before = FeeComponent.all_objects.count()
        response = self.post_file(fee_xlsx([fee_row()]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(FeeComponent.all_objects.count(), before)

    def test_a_bursar_is_not_offered_the_import_on_the_fee_list(self):
        self.client.force_login(self.north_bursar)
        response = self.client.get(reverse("fees:structure_list"))
        self.assertNotContains(response, reverse("fees:structure_import"))

    def test_signed_out_visitors_are_sent_to_the_login_page(self):
        self.client.logout()
        response = self.client.get(reverse("fees:structure_import"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_principal_is_refused_every_import_screen(self):
        """Importing fees is changing the fee structure, which a principal
        reads but does not change."""
        self.client.force_login(self.north_principal)
        for name in self.IMPORT_URLS:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_a_principal_cannot_post_a_file_either(self):
        self.client.force_login(self.north_principal)
        before = FeeComponent.all_objects.count()
        response = self.post_file(fee_xlsx([fee_row()]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(FeeComponent.all_objects.count(), before)

    def test_a_principal_is_not_offered_the_import_on_the_fee_list(self):
        self.client.force_login(self.north_principal)
        response = self.client.get(reverse("fees:structure_list"))
        self.assertNotContains(response, reverse("fees:structure_import"))

    def test_a_school_owner_picks_the_term_and_it_is_honoured(self):
        self.client.force_login(self.alpha_owner)
        response = self.client.get(reverse("fees:structure_import"))
        self.assertIn("term", response.context["form"].fields)

        # South's own Primary 1, priced under South's term.
        self.post_file(
            fee_xlsx([fee_row(school_class="Primary 1", name="Tuition")]),
            term=self.south_term.pk,
        )
        self.confirm()
        self.assertTrue(
            FeeStructure.all_objects.filter(
                school_class=self.south_p1, term=self.south_term
            ).exists()
        )

    def test_an_owner_choosing_a_term_gets_that_campuss_class_list(self):
        self.client.force_login(self.alpha_owner)
        response = self.client.get(
            reverse("fees:structure_import_template"), {"term": self.south_term.pk}
        )
        book = load_workbook(BytesIO(response.content))
        listed = {
            row[0] for row in book["Classes"].iter_rows(min_row=2, values_only=True)
        }
        self.assertEqual(listed, {"Primary 1"})   # South runs one class

    def test_a_school_owner_is_offered_one_download_per_campus_not_per_term(self):
        """Nine terms must not become nine identical buttons: the only thing a
        term changes about the template is which campus's classes it lists."""
        from .models import Term, TermSequence

        Term.all_objects.create(
            branch=self.north, name="Second Term 2025/2026",
            academic_year="2025/2026", sequence=TermSequence.SECOND,
        )
        self.client.force_login(self.alpha_owner)
        links = self.client.get(
            reverse("fees:structure_import")
        ).context["template_links"]
        self.assertEqual(
            [link["label"] for link in links],
            ["Download template — North", "Download template — South"],
        )
