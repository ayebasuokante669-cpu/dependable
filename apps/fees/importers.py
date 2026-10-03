"""Validation for the fee structure import.

The shared machinery is in :mod:`apps.core.imports`,
:mod:`apps.core.spreadsheets` and :mod:`apps.core.import_flow`, and the
reasoning behind it is written up there. Two things are only true of fees.

One row per line item, not one row per class
-------------------------------------------
The sheet is ``Class | Fee Item | Amount``, and a class with four charges takes
four rows. The alternative -- one row per class with a column per charge --
would mean us deciding in advance what a school calls its fee items, and no two
schools agree: Tuition, Development Levy, PTA, Exam Fee, Boarding, Diesel. A
fixed template cannot have a column for every one of those, and a template that
guessed at the structure of the school's own columns is exactly what we were
told not to build.

The cost is that a class's fees can be imported in part, if one of its four rows
fails. That is reported rather than hidden -- the class appears in the report
with the items that will go in and the row that will not -- and it is still
better than refusing the other eighteen classes over one bad cell.

What the term decides
---------------------
Fees are priced per class *per term*, so the term is chosen on the upload screen
the way the campus is for students. It also settles the campus, because a term
belongs to one branch: the classes a row may name are that branch's classes, and
nothing in the file can reach another one.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Max

from apps.academics.lookup import ClassIndex
from apps.core.imports import (
    Column,
    ImportReport,
    Judgement,
    Sheet,
    SourceRow,
    normalise_header,
    parse_amount,
)

from .models import FeeComponent, FeeStructure

COLUMNS: tuple[Column, ...] = (
    Column(
        "school_class", "Class", True,
        "Primary 1 — must match a class already set up at this campus",
        ("class", "class name", "classroom", "grade", "level"),
        width=52,
    ),
    # Keyed ``name`` rather than ``component`` because that is the model field
    # it feeds: Judgement.ask_the_model excludes the fields this pass already
    # refused *by model field name*, so a column key that does not match one
    # silently stops that working -- and the user is told about the same empty
    # cell twice, once in the sheet's words and once in the model's.
    Column(
        "name", "Fee Item", True,
        "Tuition — one row per charge, so a class with four charges has four rows",
        ("item", "fee", "fee name", "component", "charge", "description",
         "particulars"),
        width=60,
    ),
    Column(
        "amount", "Amount", True,
        "45000 — figures only, the Naira sign and commas are ignored",
        ("fee amount", "cost", "price", "value", "naira", "total"),
        width=48,
    ),
)

SHEET = Sheet(
    name="Fees",
    noun="fee",
    noun_plural="fees",
    filename="schoolcord-fee-import.xlsx",
    columns=COLUMNS,
    label_keys=("school_class", "name"),
    guidance_column="school_class",
)

#: A fee this large is a typo -- a hundred million Naira per term per class is
#: not a school. Caught here rather than by the field's twelve digits, so the
#: message is about money rather than about column widths.
MAX_AMOUNT = Decimal("100000000")


def validate(rows, *, term, sheet_name: str = "", unknown_columns=None,
             absent_columns=None) -> ImportReport:
    """Judge every fee row independently and return the report.

    Three queries regardless of file size: the branch's classes, the structures
    already priced for this term, and their line items.
    """
    report = ImportReport(
        sheet=SHEET,
        unknown_columns=list(unknown_columns or []),
        absent_columns=list(absent_columns or []),
        sheet_name=sheet_name,
    )
    branch = term.branch
    classes = ClassIndex(branch)

    # What this term already charges, so an import cannot quietly double a
    # charge the school set up by hand. Keyed by class and item, folded, because
    # "Tuition" and "tuition" are one charge.
    structures = {
        s.school_class_id: s
        for s in FeeStructure.objects.filter(term=term).prefetch_related("components")
    }
    priced: dict[tuple[int, str], FeeComponent] = {
        (structure.school_class_id, normalise_header(component.name)): component
        for structure in structures.values()
        for component in structure.components.all()
    }
    seen_in_file: dict[tuple[int, str], int] = {}

    for source in rows:
        report.rows.append(
            _validate_row(source, term, classes, priced, seen_in_file)
        )
    return report


def _validate_row(source: SourceRow, term, classes: ClassIndex, priced,
                  seen_in_file):
    judge = Judgement(SHEET)
    judge.require(source)

    # -- the class -----------------------------------------------------------
    school_class = None
    class_text = source.get("school_class")
    if class_text:
        school_class, problem = classes.resolve(class_text)
        if problem:
            judge.refuse("school_class", problem)

    # -- the item, and whether it is already charged -------------------------
    item = source.get("name")
    if school_class is not None and item:
        key = (school_class.pk, normalise_header(item))
        first_seen = seen_in_file.get(key)
        existing = priced.get(key)
        if first_seen is not None:
            judge.refuse(
                "name",
                f"'{item}' is already charged to "
                f"{school_class.display_name} on row {first_seen} of this file. "
                f"Put one amount per item.",
            )
        elif existing is not None:
            judge.refuse(
                "name",
                f"{school_class.display_name} is already charged '{existing.name}' "
                f"at ₦{existing.amount:,.0f} this term. Edit that fee structure "
                f"to change it.",
            )
        else:
            seen_in_file[key] = source.number

    # -- the amount ----------------------------------------------------------
    amount = None
    amount_text = source.get("amount")
    if amount_text:
        amount = parse_amount(amount_text)
        if amount is None:
            judge.refuse(
                "amount",
                f"'{amount_text}' could not be read as an amount. Use figures, "
                f"e.g. 45000 — the Naira sign and commas are fine, words are not.",
            )
        elif amount < 0:
            judge.refuse(
                "amount",
                f"'{amount_text}' is negative. A fee is what the parent owes; "
                f"a discount or a credit is recorded as a payment instead.",
            )
            amount = None
        elif amount > MAX_AMOUNT:
            judge.refuse(
                "amount",
                f"'{amount_text}' is larger than any school fee. Check for an "
                f"extra zero, or for an admission number in the Amount column.",
            )
            amount = None

    component = FeeComponent(
        school_id=term.school_id,
        branch=term.branch,
        name=item,
        amount=amount if amount is not None else Decimal("0"),
    )
    judge.ask_the_model(
        component,
        # The structure does not exist yet -- commit() creates or finds it --
        # and the amount was parsed and reported above.
        exclude={"school", "branch", "fee_structure", "amount", "position"},
    )
    return judge.verdict(source, component, school_class=school_class)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


@transaction.atomic
def commit(report: ImportReport, term) -> list[FeeComponent]:
    """Write the line items that passed, creating the structures they need.

    One transaction for the whole batch. A structure is created only when one of
    its items is going in, so a file whose Primary 1 rows all failed does not
    leave an empty Primary 1 structure behind reading ₦0 -- which the roster
    would then show as "priced" and bill nobody for.

    Positions continue from whatever the structure already holds, so an import
    that adds a charge to a hand-built structure lands at the bottom of the list
    rather than in the middle of it.
    """
    created: list[FeeComponent] = []
    structures: dict[int, FeeStructure] = {}
    positions: dict[int, int] = {}

    for row in report.ready:
        school_class = row.extra["school_class"]
        structure = structures.get(school_class.pk)
        if structure is None:
            structure, _ = FeeStructure.objects.get_or_create(
                school_class=school_class,
                term=term,
                defaults={
                    "school_id": term.school_id,
                    "branch": school_class.branch,
                },
            )
            structures[school_class.pk] = structure
            highest = structure.components.aggregate(top=Max("position"))["top"]
            positions[school_class.pk] = -1 if highest is None else highest

        positions[school_class.pk] += 1
        component = row.record
        component.fee_structure = structure
        component.branch = structure.branch
        component.school_id = structure.school_id
        component.position = positions[school_class.pk]
        component.save()
        created.append(component)

    return created


# ---------------------------------------------------------------------------
# The report's own arithmetic
# ---------------------------------------------------------------------------


def grouped_ready(report: ImportReport) -> list[dict]:
    """The accepted rows gathered by class, with each class's total.

    The review screen shows this rather than a flat list of line items, because
    the question a bursar is actually asking before they press the button is
    "what will Primary 1 cost?" -- and a column of twenty items in sheet order
    does not answer it.
    """
    groups: dict[int, dict] = {}
    for row in report.ready:
        school_class = row.extra["school_class"]
        group = groups.setdefault(
            school_class.pk,
            {"school_class": school_class, "items": [], "total": Decimal("0")},
        )
        group["items"].append(row)
        group["total"] += row.record.amount
    return sorted(
        groups.values(),
        key=lambda g: (
            g["school_class"].level,
            g["school_class"].year_in_level,
            g["school_class"].stream,
            g["school_class"].name,
        ),
    )
