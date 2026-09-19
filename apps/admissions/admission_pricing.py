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
THE BANDS, AND WHAT DIFFERS INSIDE THEM
--------------------------------------------------------------------------
The school prices intake by band -- nursery, primary, junior, senior -- and
each band's compulsory items are one sheet. Two things vary *inside* a band,
which is why some bands below are several specs rather than one:

* The book list is per class (Pre-KG 30,000 through KG 3 45,000) and, at
  senior level, per arm (Arts 70,000, Science 75,000). Books are BOOKS lines,
  summed separately on every screen and excluded from "due at intake".
* Senior charges "Practical" where the younger bands charge "Sportswear", and
  the junior and senior sheets quote "Uniform (2)" -- two sets. The names here
  are the school's own, not tidied.

The Pre-KG-KG 3 cross-check is the known discrepancy: the school's written
total is 81,000 while its own line items sum to 82,000. The line items are
seeded as supplied and the seeder warns; no balancing line has been invented,
exactly as Primary 1's 2,000 gap is handled in apps/fees/pricing.py.

Any line still marked PENDING is refused by the seeder rather than seeded as a
zero -- a real-looking 0 in front of a parent is worse than a command that
names the figure it is waiting for. Nothing is PENDING today.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: The intake year these sheets price. Matches the termly file's academic year,
#: but is stored on its own field -- an intake fee and a term fee for the same
#: year are still different things.
ACADEMIC_YEAR = "2026/2027"

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


#: The compulsory items each band charges at intake, in the order the school's
#: own sheet lists them. The book list is added per class below, because that
#: is the one line that differs inside a band.
NURSERY_INTAKE: tuple[AdmissionLine, ...] = (
    line("Admission Fee", 3_000, ADMISSION),
    line("Tuition", 24_000),
    line("Uniform", 9_000),
    line("Sportswear", 16_000),
    line("Development Levy", 2_000),
    line("Exam / Dossier", 3_000),
    line("Track suit", 25_000),
)

PRIMARY_INTAKE: tuple[AdmissionLine, ...] = (
    line("Admission Fee", 3_000, ADMISSION),
    line("Tuition", 26_000),
    line("Uniform", 10_000),
    line("Sportswear", 16_000),
    line("Development Levy", 2_000),
    line("Exam / Dossier", 3_000),
    line("Track suit", 25_000),
)

JUNIOR_INTAKE: tuple[AdmissionLine, ...] = (
    line("Admission Fee", 3_000, ADMISSION),
    line("Tuition", 31_000),
    # "(2)" is the school's own shorthand for two sets, and is kept.
    line("Uniform (2)", 24_000),
    line("Sportswear", 10_000),
    line("Development Levy", 2_000),
    line("Exam / Dossier", 3_000),
    line("Track suit", 25_000),
)

#: Senior charges Practical where the younger bands charge Sportswear. Not a
#: rename of the same line -- a different thing, at a different price.
SENIOR_INTAKE: tuple[AdmissionLine, ...] = (
    line("Admission Fee", 3_000, ADMISSION),
    line("Tuition", 40_000),
    line("Uniform (2)", 30_000),
    line("Development Levy", 2_000),
    line("Practical", 3_000),
    line("Exam / Dossier", 3_000),
    line("Track suit", 25_000),
)


def books(amount: int) -> AdmissionLine:
    """The compulsory book list, quoted by the school as a separate add-on."""
    return line("Compulsory Books", amount, BOOKS)


NURSERY_NOTE = (
    "School's written total is 81,000 but its own line items sum to "
    "82,000 -- a 1,000 gap being confirmed with the client. Line items "
    "are seeded as supplied; no balancing line has been invented. Same "
    "handling as the Primary 1 gap in apps/fees/pricing.py."
)


ADMISSION_FEES: tuple[AdmissionSpec, ...] = (
    # --- Nursery -----------------------------------------------------------
    # One sheet for the band; four specs because the book list is per class.
    # The written total carries the 1,000 discrepancy, so every class in the
    # band cross-checks against it and the seeder warns four times, once per
    # class it actually wrote.
    AdmissionSpec(
        ("Pre-KG",), NURSERY_INTAKE + (books(30_000),),
        client_total=naira(81_000), total_note=NURSERY_NOTE,
    ),
    AdmissionSpec(
        ("KG 1",), NURSERY_INTAKE + (books(32_000),),
        client_total=naira(81_000), total_note=NURSERY_NOTE,
    ),
    AdmissionSpec(
        ("KG 2",), NURSERY_INTAKE + (books(35_000),),
        client_total=naira(81_000), total_note=NURSERY_NOTE,
    ),
    AdmissionSpec(
        ("KG 3",), NURSERY_INTAKE + (books(45_000),),
        client_total=naira(81_000), total_note=NURSERY_NOTE,
    ),

    # --- Primary -----------------------------------------------------------
    AdmissionSpec(
        ("Primary 1", "Primary 2", "Primary 3", "Primary 4", "Primary 5",
         "Primary 6"),
        PRIMARY_INTAKE + (books(50_000),),
        client_total=naira(85_000),
    ),

    # --- Junior secondary ---------------------------------------------------
    AdmissionSpec(
        ("JSS 1", "JSS 2", "JSS 3"),
        JUNIOR_INTAKE + (books(55_000),),
        client_total=naira(98_000),
    ),

    # --- Senior secondary ---------------------------------------------------
    # The compulsory items are the same for both arms; only the book list
    # differs, which is why this is two specs rather than one. The termly file
    # splits SSS 2 and SSS 3 by arm for the same reason.
    AdmissionSpec(
        ("SSS 1", "SSS 2", "SSS 3"),
        SENIOR_INTAKE + (books(70_000),),
        streams=("Arts",),
        client_total=naira(106_000),
    ),
    AdmissionSpec(
        ("SSS 1", "SSS 2", "SSS 3"),
        SENIOR_INTAKE + (books(75_000),),
        streams=("Science",),
        client_total=naira(106_000),
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
