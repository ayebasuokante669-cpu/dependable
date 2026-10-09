"""Confirming a payment that has a receipt attached.

Nothing in the system reads the slip, so the person confirming has to. What must
hold: confirming money on the strength of an attached receipt needs a conscious
"I have checked this" -- never pre-ticked -- and without it nothing is confirmed
and no balance moves. Where there is no slip, or the payment is only being held
as pending, or it was confirmed already, nobody is asked.
"""

from __future__ import annotations

import shutil
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Payment, PaymentLabel, PaymentMethod, PaymentStatus
from .tests import FIFTY_K, PaymentsTestCase

PNG = b"\x89PNG\r\n\x1a\n fake"


def slip(name="slip.png"):
    return SimpleUploadedFile(name, PNG, content_type="image/png")


class ReceiptCheckTests(PaymentsTestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def pending_with_slip(self, student=None, amount="20000"):
        return self.pay(
            student or self.ada, amount, status=PaymentStatus.PENDING,
            receipt=slip(), label=PaymentLabel.SCHOOL_FEES,
            method=PaymentMethod.TRANSFER,
        )

    def confirm_data(self, payment, **extra):
        data = {
            "student": payment.student.admission_number,
            "amount": str(payment.amount),
            "date_paid": payment.date_paid.isoformat(),
            "label": payment.label,
            "method": payment.method,
            "confirm_now": "on",
        }
        data.update(extra)
        return data

    # --- The pending queue's confirm step ------------------------------------

    def test_confirming_a_slip_without_the_check_is_refused(self):
        payment = self.pending_with_slip()
        self.client.force_login(self.bursar)

        response = self.client.post(
            reverse("payments:payment_update", args=[payment.pk]),
            self.confirm_data(payment),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("receipt_checked", response.context["form"].errors)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PENDING)
        self.assertIsNone(payment.confirmed_by)
        # No balance moved: Ada still owes the whole term.
        self.assertEqual(self.position(self.ada).outstanding, FIFTY_K)

    def test_confirming_a_slip_with_the_check_goes_through(self):
        payment = self.pending_with_slip()
        self.client.force_login(self.bursar)

        response = self.client.post(
            reverse("payments:payment_update", args=[payment.pk]),
            self.confirm_data(payment, receipt_checked="on"),
        )

        self.assertRedirects(response, reverse("payments:receipt", args=[payment.pk]))
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.CONFIRMED)
        self.assertEqual(payment.confirmed_by, self.bursar)
        self.assertEqual(self.position(self.ada).outstanding, Decimal("30000"))

    def test_a_principal_confirming_is_asked_too(self):
        payment = self.pending_with_slip()
        self.client.force_login(self.principal)
        response = self.client.post(
            reverse("payments:payment_update", args=[payment.pk]),
            self.confirm_data(payment),
        )
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PENDING)

    def test_leaving_it_pending_needs_no_check(self):
        payment = self.pending_with_slip()
        self.client.force_login(self.bursar)
        data = self.confirm_data(payment)
        del data["confirm_now"]

        response = self.client.post(
            reverse("payments:payment_update", args=[payment.pk]), data
        )

        self.assertRedirects(response, reverse("payments:pending"))
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PENDING)

    def test_a_pending_payment_with_no_slip_needs_no_check(self):
        payment = self.pay(self.ada, "20000", status=PaymentStatus.PENDING)
        self.client.force_login(self.bursar)
        response = self.client.post(
            reverse("payments:payment_update", args=[payment.pk]),
            self.confirm_data(payment),
        )
        self.assertRedirects(response, reverse("payments:receipt", args=[payment.pk]))

    def test_correcting_an_already_confirmed_payment_needs_no_check(self):
        """The check is on the way *into* confirmed. A later correction was
        checked when it was confirmed."""
        payment = self.pay(
            self.ada, "20000", receipt=slip(), label=PaymentLabel.SCHOOL_FEES,
            method=PaymentMethod.TRANSFER,
        )
        self.client.force_login(self.bursar)
        response = self.client.post(
            reverse("payments:payment_update", args=[payment.pk]),
            self.confirm_data(payment, note="Teller stamp on the back"),
        )
        self.assertRedirects(response, reverse("payments:receipt", args=[payment.pk]))

    # --- Recording a new payment ---------------------------------------------

    def record(self, **extra):
        data = {
            "student": self.ada.admission_number,
            "amount": "20000",
            "date_paid": timezone.localdate().isoformat(),
            "label": PaymentLabel.SCHOOL_FEES,
            "method": PaymentMethod.TRANSFER,
            "confirm_now": "on",
        }
        data.update(extra)
        return self.client.post(reverse("payments:record"), data)

    def test_recording_a_confirmed_payment_with_a_slip_needs_the_check(self):
        self.client.force_login(self.bursar)
        response = self.record(receipt=slip())
        self.assertEqual(response.status_code, 200)
        self.assertIn("receipt_checked", response.context["form"].errors)
        self.assertFalse(Payment.all_objects.exists())

    def test_recording_a_confirmed_payment_with_no_slip_needs_no_check(self):
        self.client.force_login(self.bursar)
        self.record()
        self.assertEqual(Payment.all_objects.get().status, PaymentStatus.CONFIRMED)

    def test_holding_a_new_slip_as_pending_needs_no_check(self):
        """Sending it to the queue is how you defer the check to a person."""
        self.client.force_login(self.bursar)
        data = {"receipt": slip()}
        response = self.client.post(reverse("payments:record"), {
            "student": self.ada.admission_number,
            "amount": "20000",
            "date_paid": timezone.localdate().isoformat(),
            "label": PaymentLabel.SCHOOL_FEES,
            "method": PaymentMethod.TRANSFER,
            **data,
        })
        self.assertRedirects(response, reverse("payments:pending"))
        self.assertEqual(Payment.all_objects.get().status, PaymentStatus.PENDING)

    # --- What the screens say ------------------------------------------------

    def test_the_review_screen_names_the_student_and_amount_to_check(self):
        payment = self.pending_with_slip()
        self.client.force_login(self.bursar)
        response = self.client.get(
            reverse("payments:payment_update", args=[payment.pk])
        )
        self.assertContains(response, "Before you confirm, check the receipt")
        self.assertContains(response, self.ada.full_name)
        self.assertContains(response, self.ada.admission_number)
        self.assertContains(response, 'name="receipt_checked"')
        # Never arrives ticked: a pre-ticked box is a blind confirm.
        self.assertFalse(response.context["form"]["receipt_checked"].value())

    def test_the_review_screen_without_a_slip_does_not_ask(self):
        payment = self.pay(self.ada, "20000", status=PaymentStatus.PENDING)
        self.client.force_login(self.bursar)
        response = self.client.get(
            reverse("payments:payment_update", args=[payment.pk])
        )
        self.assertNotContains(response, 'name="receipt_checked"')

    def test_the_queue_says_receipts_are_checked_by_a_person(self):
        self.pending_with_slip()
        self.client.force_login(self.bursar)
        response = self.client.get(reverse("payments:pending"))
        self.assertContains(response, "checked by a person")
