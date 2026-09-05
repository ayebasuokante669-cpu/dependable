"""Fulfilled Academy's admission requirement fees -- what it costs to come in.

The single source for seeding intake fees, exactly as
``apps/fees/pricing.py`` is for termly fees and ``academics/curriculum.py`` is
for classes: edit the tuples below and re-run
``manage.py seed_admission_fees --replace``.

**This is not the termly fee schedule and must never be merged into it.** A
``FeeStructure`` is what a class owes *per term* and is repriced every term;
this is charged once, at the door, and carries items -- uniform, tracksuit, the
book list -- that a returning child does not pay again. Two files, two models,
two seeders, and neither command touches the other's rows.

Three things to know about how the data is expressed:

* A spec listing several class names produces *one schedule per class*, not one
  shared row -- the same rule the termly pricing file follows, so any one class
  can be repriced without dragging the others with it.
* ``client_total`` is a cross-check, never stored. Where the school's written
  total disagrees with the line items, the seeder charges the line items and
  prints a warning. Inventing a balancing line to make the arithmetic work
  would bury a real question about the source data. That is how Primary 1's
  2,000 gap is handled in the termly file, and it is how the Pre-KG-KG3 gap
  below is handled here.
* Compulsory books are ``BOOKS`` lines rather than ``INTAKE`` ones, because the
  school quotes them as a separate add-on list. They are summed separately on
  every screen and left out of "due at intake", which is what enrolment is
  gated on.

--------------------------------------------------------------------------
AMOUNTS STILL TO BE FILLED IN
--------------------------------------------------------------------------
The line-item *names* below are the sheet's; the amounts are not, because the
sheet's figures were not supplied with the module. Every unpriced line is
marked ``PENDING`` and the seeder refuses to seed a schedule that contains one,
naming it instead.

That refusal is deliberate. Seeding a zero would put a real-looking 0 in front
of a parent, and inventing a plausible figure would put a wrong one in front of
them -- both are worse than a command that says exactly which numbers it is
waiting for. Replace each ``PENDING`` with ``naira(...)`` and re-run; nothing
else needs touching.

The one figure that *is* known is the Pre-KG-KG3 cross-check: the school's
written total is 81,000 while its own line items sum to 82,000. That is
recorded on the spec so the warning fires the moment the amounts land.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: The intake year these sheets price. Matches the termly file's academic year,
#: but is stored on its own field -- an intake fee and a term fee for the same
#: year are still different things.
ACADEMIC_YEAR = "2025/2026"

#: Kind values, mirrored from ``models.AdmissionFeeKind``. Repeated as plain
#: strings so this file stays importable without Django configured, which is
#: what lets a test read the sheet without a database.
ADMISSION = "admission"
INTAKE = "intake"
BOOKS = "books"

#: An amount the school has not supplied yet. See the module docstring: the
#: seeder names these rather than guessing at them.
PENDING = None


@dataclass(frozen=True)
class AdmissionLine:
    name: str
    amount: Decimal | None
    kind: str = INTAKE

    @property
    def is_priced(self) -> bool:
        return self.amount is not None


def naira(amount: int | str) -> Decimal:
    """Decimal from a string or int, never a float -- money must not carry
    binary rounding error."""
    return Decimal(str(amount))


def line(name: str, amount: int | str | None, kind: str = INTAKE) -> AdmissionLine:
    return AdmissionLine(
        name=name, amount=None if amount is None else naira(amount), kind=kind
    )


@dataclass(frozen=True)
class AdmissionSpec:
    """One intake fee sheet, applied to each of ``class_names`` separately."""

    class_names: tuple[str, ...]
    lines: tuple[AdmissionLine, ...]
    #: Empty means every arm of the named class; otherwise only these arms.
    streams: tuple[str, ...] = ()
    #: The school's own written total for the compulsory items, for
    #: cross-checking only. Books are quoted separately and excluded.
    client_total: Decimal | None = None
    #: Why the two disagree, printed by the seeder when they do.
    total_note: str = ""

    @property
    def pending_lines(self) -> tuple[AdmissionLine, ...]:
        return tuple(item for item in self.lines if not item.is_priced)

    @property
    def is_complete(self) -> bool:
        return not self.pending_lines

    @property
    def computed_total(self) -> Decimal:
        """Admission plus intake -- what the sheet's own total line covers."""
        return sum(
            (
                item.amount
                for item in self.lines
                if item.is_priced and item.kind in (ADMISSION, INTAKE)
            ),
            Decimal("0"),
        )

    def matches(self, klass) -> bool:
        if klass.name not in self.class_names:
            return False
        return not self.streams or klass.stream in self.streams


#: The sheet's own line items, in the order it lists them. Names are the
#: school's; amounts are PENDING until the figures are supplied.
ADMISSION_FEES: tuple[AdmissionSpec, ...] = (
    # --- Nursery -----------------------------------------------------------
    # One sheet for the whole band, per the school's own grouping.
    AdmissionSpec(
        ("Pre-KG", "KG 1", "KG 2", "KG 3"),
        (
            line("Admission Fee", PENDING, ADMISSION),
            line("Tuition", PENDING),
            line("Uniform", PENDING),
            line("Sportswear / Development Levy", PENDING),
            line("Exam / Dossier", PENDING),
            line("Tracksuit", PENDING),
            line("Compulsory Books", PENDING, BOOKS),
        ),
        client_total=naira(81_000),
        total_note=(
            "School's written total is 81,000 but its own line items sum to "
            "82,000 -- a 1,000 gap being confirmed with the client. Line items "
            "are seeded as supplied; no balancing line has been invented. Same "
            "handling as the Primary 1 gap in apps/fees/pricing.py."
        ),
    ),

    # --- Primary -----------------------------------------------------------
    AdmissionSpec(
        ("Primary 1", "Primary 2", "Primary 3", "Primary 4", "Primary 5",
         "Primary 6"),
        (
            line("Admission Fee", PENDING, ADMISSION),
            line("Tuition", PENDING),
            line("Uniform", PENDING),
            line("Sportswear / Development Levy", PENDING),
            line("Exam / Dossier", PENDING),
            line("Tracksuit", PENDING),
            line("Compulsory Books", PENDING, BOOKS),
        ),
    ),

    # --- Junior secondary ---------------------------------------------------
    AdmissionSpec(
        ("JSS 1", "JSS 2", "JSS 3"),
        (
            line("Admission Fee", PENDING, ADMISSION),
            line("Tuition", PENDING),
            line("Uniform", PENDING),
            line("Sportswear / Development Levy", PENDING),
            line("Exam / Dossier", PENDING),
            line("Tracksuit", PENDING),
            line("Compulsory Books", PENDING, BOOKS),
        ),
    ),

    # --- Senior secondary ---------------------------------------------------
    # The termly file prices SSS 2 and SSS 3 differently per arm. The admission
    # sheet does not split by arm, so one spec covers both -- if the client's
    # sheet turns out to differ by arm, add `streams=("Science",)` specs here
    # exactly as apps/fees/pricing.py does.
    AdmissionSpec(
        ("SSS 1", "SSS 2", "SSS 3"),
        (
            line("Admission Fee", PENDING, ADMISSION),
            line("Tuition", PENDING),
            line("Uniform", PENDING),
            line("Sportswear / Development Levy", PENDING),
            line("Exam / Dossier", PENDING),
            line("Tracksuit", PENDING),
            line("Compulsory Books", PENDING, BOOKS),
        ),
    ),
)


def specs_for(klass) -> list[AdmissionSpec]:
    """Every spec that should produce a schedule for ``klass``."""
    return [spec for spec in ADMISSION_FEES if spec.matches(klass)]


def pending_report() -> list[tuple[str, tuple[str, ...]]]:
    """Which sheets are still waiting on figures, and which lines.

    Used by the seeder to print a precise list rather than a vague failure, and
    by the test suite to assert that an incomplete sheet is refused rather than
    seeded as zeros.
    """
    report: list[tuple[str, tuple[str, ...]]] = []
    for spec in ADMISSION_FEES:
        if spec.is_complete:
            continue
        label = ", ".join(spec.class_names)
        report.append((label, tuple(item.name for item in spec.pending_lines)))
    return report
