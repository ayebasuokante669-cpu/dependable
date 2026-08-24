"""A student's fee position, derived rather than stored.

What a student owes this term is the total of their class's ``FeeStructure``
for the term their branch is currently in. That is a fact about the class and
the term, so it lives on those models; the student just points at them. Nothing
here writes anything.

The reason this module exists at all, rather than a property on ``Student``, is
the list screen: rendering fifty students must not mean fifty structure lookups.
:class:`FeeSchedule` loads the term and the class totals in two queries and then
answers for any number of students in memory.

``paid`` comes from the payments app when it is installed, and is zero when it
is not -- which is honest rather than broken: nothing has been recorded, so
every priced student reads as unpaid. The lookup goes through
:func:`_paid_amounts`, which resolves the payments app lazily. A hard import
would make the roster unrenderable until payments ships, and this module is
older than that app by several branches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable

from django.apps import apps as django_apps
from django.db.models import Sum
from django.utils import timezone

from apps.fees.models import FeeComponent, FeeStructure, Term

ZERO = Decimal("0")


class FeeState:
    """The states a student's fees can be in on screen.

    ``UNPRICED`` is not a payment state -- it means nobody has set fees for that
    class this term, which is a setup gap the school needs to see rather than a
    zero balance to be reassured by.

    ``OVERDUE`` is not a fifth position either: it is ``UNPAID`` or ``PARTIAL``
    after the term's due date has passed. A term with no due date set never
    produces it, because a deadline nobody stated is not one a parent can have
    missed.
    """

    UNPRICED = "unpriced"
    PAID = "paid"
    PARTIAL = "partial"
    UNPAID = "unpaid"
    OVERDUE = "overdue"


#: Design-system pill class and label for each state -- the four payment-status
#: colours from the token set, and nothing invented alongside them.
STATE_DISPLAY = {
    FeeState.UNPRICED: ("status-unpaid", "No fees set"),
    FeeState.PAID: ("status-paid", "Paid"),
    FeeState.PARTIAL: ("status-partial", "Part paid"),
    FeeState.UNPAID: ("status-unpaid", "Unpaid"),
    FeeState.OVERDUE: ("status-overdue", "Overdue"),
}


@dataclass(frozen=True)
class FeePosition:
    """Where one student stands against one term's fees."""

    term: Term | None
    structure: FeeStructure | None
    expected: Decimal = ZERO
    #: Recorded payments. Always zero until the payments layer lands.
    paid: Decimal = ZERO

    @property
    def is_priced(self) -> bool:
        return self.structure is not None

    @property
    def outstanding(self) -> Decimal:
        """Never negative: an overpayment is a credit, not a negative balance."""
        return max(self.expected - self.paid, ZERO)

    @property
    def due_date(self):
        """When this term's fees were expected, or ``None`` if never stated."""
        return getattr(self.term, "due_date", None)

    @property
    def is_overdue(self) -> bool:
        """Something is still owed and the school's own deadline has passed."""
        if self.outstanding <= ZERO or self.due_date is None:
            return False
        return timezone.localdate() > self.due_date

    @property
    def state(self) -> str:
        if not self.is_priced:
            return FeeState.UNPRICED
        if self.expected <= ZERO or self.paid >= self.expected:
            return FeeState.PAID
        # Overdue outranks the amount-based states: a bursar looking at this
        # column needs the deadline to be the thing that stands out.
        if self.is_overdue:
            return FeeState.OVERDUE
        if self.paid > ZERO:
            return FeeState.PARTIAL
        return FeeState.UNPAID

    @property
    def pill_class(self) -> str:
        return STATE_DISPLAY[self.state][0]

    @property
    def pill_label(self) -> str:
        return STATE_DISPLAY[self.state][1]


@dataclass
class FeeSchedule:
    """Every current term and class total the caller needs, loaded once.

    Built by :func:`load`, then asked ``position_for(student)`` as many times as
    there are rows on the page.
    """

    #: branch_id -> the term that branch is currently in.
    terms: dict[int, Term] = field(default_factory=dict)
    #: class_id -> its structure for that term. Absent means "not priced".
    structures: dict[int, FeeStructure] = field(default_factory=dict)
    #: class_id -> sum of the structure's components.
    totals: dict[int, Decimal] = field(default_factory=dict)
    #: student_id -> amount paid. The seam the payments layer plugs into.
    payments: dict[int, Decimal] = field(default_factory=dict)

    def term_for(self, student) -> Term | None:
        return self.terms.get(student.branch_id)

    def position_for(self, student) -> FeePosition:
        term = self.term_for(student)
        if term is None:
            return FeePosition(term=None, structure=None)
        structure = self.structures.get(student.school_class_id)
        if structure is None or structure.term_id != term.pk:
            # A structure from a different term is not this student's position.
            return FeePosition(term=term, structure=None)
        return FeePosition(
            term=term,
            structure=structure,
            expected=self.totals.get(student.school_class_id, ZERO),
            paid=self.payments.get(student.pk, ZERO),
        )


def load(
    students: Iterable, *, payments: dict[int, Decimal] | None = None
) -> FeeSchedule:
    """Build the schedule covering ``students``, in two queries.

    Both querysets go through tenant-scoped managers, so a caller can never
    assemble a position out of another tenant's pricing.
    """
    students = list(students)
    if not students:
        return FeeSchedule()

    branch_ids = {s.branch_id for s in students if s.branch_id}
    class_ids = {s.school_class_id for s in students if s.school_class_id}

    terms = {
        term.branch_id: term
        for term in Term.objects.filter(is_current=True, branch_id__in=branch_ids)
    }
    if not terms:
        return FeeSchedule()

    structures = {
        structure.school_class_id: structure
        for structure in FeeStructure.objects.filter(
            term__in=terms.values(), school_class_id__in=class_ids
        ).select_related("term")
    }

    # One aggregate over the line items rather than FeeStructure.total per row:
    # the property is cheap with a prefetch, but a page of fifty students
    # touching twelve classes should still be one query, not twelve.
    totals = {
        row["fee_structure__school_class_id"]: row["amount"] or ZERO
        for row in FeeComponent.objects.filter(
            fee_structure_id__in=[s.pk for s in structures.values()]
        )
        .values("fee_structure__school_class_id")
        .annotate(amount=Sum("amount"))
    }

    if payments is None:
        payments = _paid_amounts(students, terms)

    return FeeSchedule(
        terms=terms, structures=structures, totals=totals, payments=payments
    )


def _paid_amounts(students, terms: dict) -> dict[int, Decimal]:
    """Confirmed payments per student, for the term their branch is in.

    Resolved through the app registry rather than an import, so this module has
    no dependency on the payments app and keeps working when it is absent. The
    payments layer owns the definition of what counts -- confirmed only, this
    term only -- and this file does not second-guess it.
    """
    if not django_apps.is_installed("apps.payments"):
        return {}
    from apps.payments.balances import paid_by_student

    return paid_by_student(students, terms)


def position_for(student) -> FeePosition:
    """The fee position of a single student -- the detail screen's entry point."""
    return load([student]).position_for(student)
