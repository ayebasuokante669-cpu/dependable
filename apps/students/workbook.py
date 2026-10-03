"""The student sheet's spreadsheet half: its drop-downs and its class list.

The openpyxl work itself moved to :mod:`apps.core.spreadsheets` when the class,
subject and fee-structure imports needed the same reader. What is left here is
the part that is only true of the student sheet -- which columns get a
drop-down, and the reference tab listing the branch's classes -- plus the names
the student import has always been called by, so the views and the tests did not
have to move with it.
"""

from __future__ import annotations

from apps.core.spreadsheets import (  # noqa: F401  (re-exported)
    HEADER_SEARCH_DEPTH,
    Choice,
    ParsedSheet,
    Reference,
    WorkbookError,
)
from apps.core.spreadsheets import build_template as _build_template
from apps.core.spreadsheets import read_rows as _read_rows

from .importer import SHEET
from .models import StudentStatus

SHEET_NAME = SHEET.name
REFERENCE_SHEET_NAME = "Classes"
TEMPLATE_FILENAME = SHEET.filename
HEADER_MATCH_THRESHOLD = SHEET.header_match_threshold


def choices() -> tuple[Choice, ...]:
    """The two columns with a short, fixed set of answers."""
    return (
        Choice("sex", ("Male", "Female")),
        Choice("status", tuple(label for _, label in StudentStatus.choices)),
    )


def references(class_names: list[str] | None) -> tuple[Reference, ...]:
    """The branch's real classes, on a tab of their own.

    Unknown classes are the error this import produces most, and the cheapest
    place to prevent one is before it is typed. The drop-down is a warning
    rather than a hard stop: a school pasting a column of 300 class names
    should not have Excel refuse every one of them.
    """
    if not class_names:
        return ()
    return (
        Reference(
            name=REFERENCE_SHEET_NAME,
            title="Classes at this branch — use one of these in the Class column",
            values=tuple(class_names),
            column="school_class",
            error=(
                "That class does not exist at this branch. Pick one from the "
                "list, or set the class up in SCHOOLCORD first."
            ),
            error_title="Unknown class",
        ),
    )


def build_template(class_names: list[str] | None = None) -> bytes:
    """The blank student import sheet, as ``.xlsx`` bytes."""
    return _build_template(
        SHEET, choices=choices(), references=references(class_names)
    )


def read_rows(uploaded) -> ParsedSheet:
    """Parse an uploaded student workbook, or raise :class:`WorkbookError`."""
    return _read_rows(uploaded, SHEET)
