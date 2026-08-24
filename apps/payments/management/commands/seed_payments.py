"""Seed a realistic spread of payments for a branch's existing students.

    python manage.py seed_payments
    python manage.py seed_payments --replace

Idempotent: a branch that already has seeded payments is left alone unless
``--replace`` is passed, which clears the term's payments first.

The spread is chosen so that every state the screens can show actually appears,
because a demo where everyone is unpaid proves nothing about the balance logic:

* some students paid in full, in one payment or in two instalments;
* some part paid, so ``partial`` and a real outstanding figure show up;
* some untouched, so ``unpaid`` is on screen;
* two pending receipts with attachments, so the queue has something in it and
  the "does not count until confirmed" rule is visible;
* one voided payment, so a struck-through row and its reason are there to see.

Every amount is derived from the student's own class fee structure rather than
hard-coded, so re-pricing a class re-seeds sensible figures instead of nonsense.
"""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.fees.models import FeeComponent, FeeStructure, Term
from apps.payments.models import (
    Payment,
    PaymentLabel,
    PaymentMethod,
    PaymentStatus,
)
from apps.schools.models import Branch, School
from apps.students.models import Student, StudentStatus

ZERO = Decimal("0")

#: Fixed, so a re-seed produces the same demo rather than a different one every
#: time somebody reloads the screens.
SEED = 20250824

#: How each student in the rotation pays. The cycle is deliberately not a round
#: number of classes, so a class does not end up uniformly one colour.
PLAN = (
    "full",         # one payment, settled
    "part",         # about half
    "none",         # nothing at all
    "instalments",  # two payments adding up to the whole fee
    "part",
    "full",
    "none",
    "small",        # a token payment against a big fee
    "full",
    "part",
    "none",
)

#: A one-pixel PNG. Enough for the pending queue to show a real attachment and
#: exercise the image branch of the template without committing a photo of a
#: bank slip to the repository.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006"
    "000557bfabd40000000049454e44ae426082"
)


class Command(BaseCommand):
    help = "Create a realistic spread of payments for a branch's students."

    def add_arguments(self, parser):
        parser.add_argument("--school", default="Fulfilled Academy")
        parser.add_argument("--branch", default="Main Campus")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete this term's existing payments before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        school, branch, term = self._resolve_target(options)
        self.stdout.write(f"Seeding {school.name} / {branch.name} / {term.name}\n")

        existing = Payment.all_objects.filter(term=term)
        if existing.exists():
            if not options["replace"]:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {existing.count()} payment(s) already recorded for "
                        f"this term. Nothing done. Pass --replace to reseed."
                    )
                )
                return
            count = existing.count()
            # Delete the uploaded files as well as the rows. Django does not do
            # this for you, and a seeder that is meant to be re-run would
            # otherwise leave a slip behind in media/ every single time.
            for payment in existing.exclude(receipt=""):
                if payment.receipt:
                    payment.receipt.delete(save=False)
            existing.delete()
            self.stdout.write(f"  cleared {count} existing payment(s)")

        totals = self._class_totals(term)
        students = list(
            Student.all_objects.filter(
                branch=branch, status=StudentStatus.ACTIVE
            ).select_related("school_class").order_by("admission_number")
        )
        if not students:
            raise CommandError(
                f"{branch.name} has no active students. Run seed_students first."
            )

        rng = random.Random(SEED)
        created = self._seed(students, totals, term, branch, rng)
        self._report(term, created)

    # -- target -----------------------------------------------------------

    def _resolve_target(self, options):
        school = School.all_objects.filter(name=options["school"]).first()
        if school is None:
            raise CommandError(f'No school named "{options["school"]}".')
        branch = Branch.all_objects.filter(
            school=school, name=options["branch"]
        ).first()
        if branch is None:
            raise CommandError(
                f'No branch named "{options["branch"]}" at {school.name}.'
            )
        term = Term.all_objects.filter(branch=branch, is_current=True).first()
        if term is None:
            raise CommandError(
                f"{branch.name} has no current term. Run seed_fees first."
            )
        return school, branch, term

    def _class_totals(self, term) -> dict[int, Decimal]:
        """class_id -> the fee structure total, in one query."""
        structures = FeeStructure.all_objects.filter(term=term)
        return {
            row["fee_structure__school_class_id"]: row["amount"] or ZERO
            for row in FeeComponent.all_objects.filter(fee_structure__in=structures)
            .values("fee_structure__school_class_id")
            .annotate(amount=Sum("amount"))
        }

    # -- seeding ----------------------------------------------------------

    def _seed(self, students, totals, term, branch, rng) -> list[Payment]:
        created: list[Payment] = []
        today = timezone.localdate()
        # Term start, roughly: payments land across the weeks after it.
        opened = today - timedelta(days=70)

        for index, student in enumerate(students):
            expected = totals.get(student.school_class_id, ZERO)
            if expected <= ZERO:
                # An unpriced class has nothing to pay against, and inventing a
                # payment for it would make the "not priced" state disappear.
                continue

            plan = PLAN[index % len(PLAN)]
            when = opened + timedelta(days=rng.randint(0, 55))

            if plan == "none":
                continue
            if plan == "full":
                created.append(
                    self._payment(student, term, branch, expected, when, rng)
                )
            elif plan == "part":
                created.append(
                    self._payment(
                        student, term, branch, self._round(expected * Decimal("0.55")),
                        when, rng,
                    )
                )
            elif plan == "small":
                created.append(
                    self._payment(
                        student, term, branch, self._round(expected * Decimal("0.2")),
                        when, rng,
                    )
                )
            elif plan == "instalments":
                first = self._round(expected * Decimal("0.6"))
                created.append(
                    self._payment(student, term, branch, first, when, rng)
                )
                created.append(
                    self._payment(
                        student, term, branch, expected - first,
                        when + timedelta(days=rng.randint(7, 25)), rng,
                    )
                )

        self._seed_pending(students, totals, term, branch, today, created)
        self._seed_void(students, totals, term, branch, today, created)
        return created

    def _payment(self, student, term, branch, amount, when, rng, **overrides):
        method = rng.choice(
            [PaymentMethod.TRANSFER, PaymentMethod.TRANSFER,
             PaymentMethod.CASH, PaymentMethod.POS]
        )
        defaults = {
            "school": branch.school,
            "branch": branch,
            "student": student,
            "term": term,
            "amount": amount,
            "date_paid": when,
            "label": PaymentLabel.SCHOOL_FEES,
            "method": method,
            "reference": self._reference(method, rng),
            "status": PaymentStatus.CONFIRMED,
            "confirmed_at": timezone.now(),
        }
        defaults.update(overrides)
        return Payment.all_objects.create(**defaults)

    def _reference(self, method, rng) -> str:
        if method == PaymentMethod.CASH:
            return ""
        if method == PaymentMethod.POS:
            return f"POS{rng.randint(100000, 999999)}"
        return f"FT{rng.randint(10**11, 10**12 - 1)}"

    def _seed_pending(self, students, totals, term, branch, today, created):
        """Two receipts waiting to be checked, with a slip attached.

        Picked from students who have paid nothing, so confirming one visibly
        moves a balance -- which is the flow the queue exists to demonstrate.
        """
        candidates = [
            s for s in students
            if totals.get(s.school_class_id, ZERO) > ZERO
            and not any(p.student_id == s.pk for p in created)
        ]
        for offset, student in enumerate(candidates[:2]):
            expected = totals[student.school_class_id]
            payment = self._payment(
                student, term, branch,
                self._round(expected * Decimal("0.5")),
                today - timedelta(days=offset + 1),
                random.Random(SEED + offset),
                status=PaymentStatus.PENDING,
                confirmed_at=None,
                label=PaymentLabel.SCHOOL_FEES if offset == 0 else PaymentLabel.UNIFORM,
                note="Receipt handed in at the gate — needs checking.",
            )
            payment.receipt.save(
                f"transfer-slip-{payment.pk}.png", ContentFile(PNG_1PX), save=True
            )
            created.append(payment)

    def _seed_void(self, students, totals, term, branch, today, created):
        """One reversed payment, so a struck-through row and a reason exist."""
        confirmed = [p for p in created if p.status == PaymentStatus.CONFIRMED]
        if not confirmed:
            return
        student = confirmed[-1].student
        payment = self._payment(
            student, term, branch, Decimal("10000"),
            today - timedelta(days=30), random.Random(SEED),
            label=PaymentLabel.BOOKS,
            note="Entered twice by mistake.",
        )
        payment.void(reason="Duplicate entry — the parent paid once.")
        created.append(payment)

    @staticmethod
    def _round(amount: Decimal) -> Decimal:
        """To the nearest 500 naira -- schools take round money, not 27,483.21."""
        step = Decimal("500")
        return (amount / step).quantize(Decimal("1")) * step

    # -- reporting ---------------------------------------------------------

    def _report(self, term, created):
        by_status: dict[str, int] = {}
        for payment in created:
            by_status[payment.status] = by_status.get(payment.status, 0) + 1

        self.stdout.write(f"  {len(created)} payment(s) created")
        for status, label in PaymentStatus.choices:
            self.stdout.write(f"    {label.lower():<10} {by_status.get(status, 0)}")

        confirmed_total = sum(
            (p.amount for p in created if p.status == PaymentStatus.CONFIRMED), ZERO
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"  {confirmed_total:,.0f} confirmed against {term.name}"
            )
        )
