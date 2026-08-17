"""A student's fee position, derived rather than stored.

What a student owes this term is the total of their class's ``FeeStructure``
for the term their branch is currently in. That is a fact about the class and
the term, so it lives on those models; the student just points at them. Nothing
here writes anything.

The reason this module exists at all, rather than a property on ``Student``, is
the list screen: rendering fifty students must not mean fifty structure lookups.
:class:`FeeSchedule` loads the term and the class totals in two queries and then
answers for any number of students in memory.

Payments do not exist yet, so ``paid`` is always zero and every priced student
reads as unpaid. That is honest -- nothing has been recorded -- and it is the
shape the payments layer fills in: give :class:`FeeSchedule` a map of amounts
paid and the pills, balances and totals below start telling the real story
without another screen changing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable

from django.db.models import Sum

from apps.fees.models import FeeComponent, FeeStructure, Term

ZERO = Decimal("0")


class FeeState:
    """The four states a student's fees can be in on screen.

    ``UNPRICED`` is not a payment state -- it means nobody has set fees for that
    class this term, which is a setup gap the school needs to see rather than a
    zero balance to be reassured by.
    """

    UNPRICED = "unpriced"
    PAID = "paid"
    PARTIAL = "partial"
    UNPAID = "unpaid"


#: Design-system pill class and label for each state.
STATE_DISPLAY = {
    FeeState.UNPRICED: ("status-unpaid", "No fees set"),
    FeeState.PAID: ("status-paid", "Paid"),
    FeeState.PARTIAL: ("status-partial", "Part paid"),
    FeeState.UNPAID: ("status-unpaid", "Unpaid"),
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
    def state(self) -> str:
        if not self.is_priced:
            return FeeState.UNPRICED
        if self.expected <= ZERO or self.paid >= self.expected:
            return FeeState.PAID
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


def load(students: Iterable) -> FeeSchedule:
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

    return FeeSchedule(terms=terms, structures=structures, totals=totals)


def position_for(student) -> FeePosition:
    """The fee position of a single student -- the detail screen's entry point."""
    return load([student]).position_for(student)
