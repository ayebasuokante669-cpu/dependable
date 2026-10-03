"""Validation for the Excel student import.

The bulk half of step 4. A school arriving with three hundred students already
on the roll is not going to type them in one at a time, and the spreadsheet they
already keep is the closest thing they have to a database.

The machinery this uses -- the column contract, the per-row verdict, the report,
the three screens -- is shared with the class, subject and fee-structure imports
and lives in :mod:`apps.core.imports`, :mod:`apps.core.spreadsheets` and
:mod:`apps.core.import_flow`. The decisions those modules were built around are
written up there and are not restated here. What is left in this module is the
part that is only true of students:

* the thirteen columns, and the spellings a school's own roster uses for them;
* the admission number, which has to be unique against the roster *and* against
  the rest of the file;
* the leading zero Excel eats off a phone number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db import transaction

from apps.academics.lookup import ClassIndex
from apps.core.imports import (
    GUIDANCE_MARKER,
    Column,
    FieldError,
    ImportReport,
    Judgement,
    Sheet,
    SourceRow,
    normalise_header,
    parse_date,
)
from apps.core.imports import RowResult as BaseRowResult

from .models import Sex, Student, StudentStatus
from .validators import normalise_admission_number, normalise_phone

__all__ = [
    "COLUMNS", "COLUMNS_BY_KEY", "REQUIRED_KEYS", "GUIDANCE_MARKER",
    "GUIDANCE_COLUMN", "MAX_ROWS", "SHEET", "Column", "FieldError",
    "ImportReport", "RowResult", "SourceRow", "ClassIndex", "column_for_header",
    "guidance_for", "normalise_header", "parse_date", "parse_import_phone",
    "validate", "commit",
]


#: The sheet, left to right. Order here is the order in the template; matching
#: an uploaded file does not depend on it.
COLUMNS: tuple[Column, ...] = (
    Column(
        "admission_number", "Admission Number", True, "FA/2025/001",
        ("adm no", "admission no", "admno", "adm number", "student number"),
        width=28,
    ),
    Column("first_name", "First Name", True, "Chinaza",
           ("firstname", "given name", "given names"), width=16),
    Column("last_name", "Surname", True, "Okonkwo",
           ("last name", "lastname", "family name"), width=16),
    Column("other_names", "Other Names", False, "Adaeze — leave blank if none",
           ("middle name", "middle names", "other name"), width=28),
    Column("sex", "Sex", False, "Male or Female", ("gender",), width=12),
    Column("date_of_birth", "Date of Birth", False, "2019-04-12 (YYYY-MM-DD)",
           # "d.o.b." reduces to "d o b", not to "dob" -- punctuation becomes a
           # space -- so the dotted spelling is listed in its own right.
           ("dob", "d.o.b.", "birth date", "birthdate", "date born"), width=24),
    Column("date_admitted", "Date Admitted", False,
           "2025-09-08 (YYYY-MM-DD) — today's date if left blank",
           ("admission date", "date of admission", "enrolled on"), width=38),
    Column("status", "Status", False,
           "Active, Inactive, Graduated or Withdrawn — Active if left blank",
           (), width=44),
    Column("school_class", "Class", True,
           "Primary 1 — must match a class already set up at this branch",
           ("class name", "current class", "classroom", "grade"), width=46),
    Column("parent_name", "Parent / Guardian Name", True, "Mrs. Ngozi Okonkwo",
           ("parent name", "guardian name", "parent", "guardian"), width=24),
    Column("parent_phone", "Parent / Guardian Phone", True, "08034129876",
           ("parent phone", "guardian phone", "phone", "phone number",
            "parent number", "contact"), width=24),
    Column("parent_email", "Parent / Guardian Email", False,
           "ngozi.okonkwo@example.com — optional",
           ("parent email", "guardian email", "email"), width=36),
    Column("address", "Address", False, "14 Adeniyi Jones Avenue, Ikeja, Lagos",
           ("home address", "residential address"), width=38),
)


@dataclass
class RowResult(BaseRowResult):
    """The verdict on one row, and the student it would create."""

    @property
    def student(self) -> Student | None:
        """The unsaved student, under the name the roster screens use."""
        return self.record


SHEET = Sheet(
    name="Students",
    noun="student",
    noun_plural="students",
    filename="schoolcord-student-import.xlsx",
    columns=COLUMNS,
    label_keys=("admission_number", "first_name", "last_name"),
    guidance_column="admission_number",
    result_class=RowResult,
)

# Names the student import has always exposed, kept so the views, the tests and
# the template reference sheet read the same as they did before the machinery
# moved. The sheet is the single source; these are views onto it.
COLUMNS_BY_KEY: dict[str, Column] = SHEET.by_key
REQUIRED_KEYS: tuple[str, ...] = SHEET.required_keys
GUIDANCE_COLUMN = SHEET.guidance_column
MAX_ROWS = SHEET.max_rows
column_for_header = SHEET.column_for_header
guidance_for = SHEET.guidance_for


# ---------------------------------------------------------------------------
# Cell parsing
# ---------------------------------------------------------------------------

_SEX_WORDS = {
    "male": Sex.MALE, "m": Sex.MALE, "boy": Sex.MALE,
    "female": Sex.FEMALE, "f": Sex.FEMALE, "girl": Sex.FEMALE,
}

_STATUS_WORDS = {
    **{value.lower(): value for value, _ in StudentStatus.choices},
    **{label.lower(): value for value, label in StudentStatus.choices},
    "enrolled": StudentStatus.ACTIVE,
    "current": StudentStatus.ACTIVE,
    "left": StudentStatus.WITHDRAWN,
    "alumni": StudentStatus.GRADUATED,
}


def parse_import_phone(text: str) -> str:
    """Normalise a phone number, restoring the zero Excel ate.

    A column of numbers typed without a leading apostrophe becomes numeric, and
    ``08034129876`` comes back as ``8034129876``. That is not a typo the school
    made and not one they can see, so it is repaired here rather than reported.
    Only in the importer: manual entry has a visible field where the zero is
    still on screen.
    """
    digits = normalise_phone(text)
    if len(digits) == 10 and digits[0] in "789":
        digits = "0" + digits
    return digits


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(rows, *, branch, sheet_name: str = "", unknown_columns=None,
             absent_columns=None) -> ImportReport:
    """Judge every row independently and return the report.

    Nothing is written. The ``Student`` built for a passing row is left unsaved
    on its :class:`RowResult` so that :func:`commit` has nothing left to decide.

    Two queries regardless of file size: one for the branch's classes, one for
    the admission numbers already in use there.
    """
    report = ImportReport(
        sheet=SHEET,
        unknown_columns=list(unknown_columns or []),
        absent_columns=list(absent_columns or []),
        sheet_name=sheet_name,
    )
    classes = ClassIndex(branch)

    # Case-insensitively, matching the database constraint: FA/2025/001 and
    # fa/2025/001 are the same child's number.
    taken: dict[str, str] = {
        number.upper(): f"{first} {last}".strip()
        for number, first, last in Student.objects.filter(branch=branch).values_list(
            "admission_number", "first_name", "last_name"
        )
    }
    seen_in_file: dict[str, int] = {}

    for source in rows:
        result = _validate_row(source, branch, classes, taken, seen_in_file)
        report.rows.append(result)
    return report


def _validate_row(source: SourceRow, branch, classes: ClassIndex,
                  taken: dict[str, str], seen_in_file: dict[str, int]) -> RowResult:
    judge = Judgement(SHEET)
    judge.require(source)

    # -- admission number ----------------------------------------------------
    admission_number = normalise_admission_number(source.get("admission_number"))
    if admission_number:
        folded = admission_number.upper()
        first_seen = seen_in_file.get(folded)
        if first_seen is not None:
            judge.refuse(
                "admission_number",
                f"'{admission_number}' is already used on row {first_seen} of "
                f"this file.",
            )
        elif folded in taken:
            judge.refuse(
                "admission_number",
                f"'{admission_number}' already belongs to a student at "
                f"{branch.name} ({taken[folded]}).",
            )
        else:
            # Claimed even if the row fails elsewhere, so a file containing the
            # same number twice reports the *second* one as the duplicate
            # rather than blaming whichever row happened to be valid.
            seen_in_file[folded] = source.number

    # -- class ---------------------------------------------------------------
    school_class = None
    class_text = source.get("school_class")
    if class_text:
        school_class, problem = classes.resolve(class_text)
        if problem:
            judge.refuse("school_class", problem)

    # -- sex -----------------------------------------------------------------
    sex_text = source.get("sex")
    sex = ""
    if sex_text:
        matched = _SEX_WORDS.get(normalise_header(sex_text))
        if matched is None:
            judge.refuse("sex", f"'{sex_text}' is not a sex. Use Male or Female.")
        else:
            sex = matched

    # -- status --------------------------------------------------------------
    status_text = source.get("status")
    status = StudentStatus.ACTIVE
    if status_text:
        matched = _STATUS_WORDS.get(normalise_header(status_text))
        if matched is None:
            judge.refuse(
                "status",
                f"'{status_text}' is not a status. Use Active, Inactive, "
                f"Graduated or Withdrawn.",
            )
        else:
            status = matched

    # -- dates ---------------------------------------------------------------
    dates: dict[str, date | None] = {}
    for key in ("date_of_birth", "date_admitted"):
        text = source.get(key)
        if not text:
            dates[key] = None
            continue
        parsed = parse_date(text)
        if parsed is None:
            judge.refuse(
                key,
                f"'{text}' could not be read as a date. Use YYYY-MM-DD, "
                f"e.g. 2019-04-12.",
            )
        dates[key] = parsed

    # -- the model's own rules ------------------------------------------------
    # Everything above was parsing; from here the student is real enough to ask
    # the model what it thinks. Phone format, email, name lengths and the
    # born-after-admission check all live there already and are not restated.
    student = Student(
        school_id=branch.school_id,
        branch=branch,
        school_class=school_class,
        admission_number=admission_number,
        first_name=source.get("first_name"),
        last_name=source.get("last_name"),
        other_names=source.get("other_names"),
        sex=sex,
        date_of_birth=dates["date_of_birth"],
        status=status,
        parent_name=source.get("parent_name"),
        parent_phone=parse_import_phone(source.get("parent_phone")),
        parent_email=source.get("parent_email"),
        address=source.get("address"),
    )
    if dates["date_admitted"]:
        student.date_admitted = dates["date_admitted"]

    # The uniqueness check in particular is deliberately ours, because it has
    # to see the other rows of this file as well as the roster already in the
    # database.
    judge.ask_the_model(
        student,
        exclude={"school", "branch", "school_class", "sex", "status"},
    )
    return judge.verdict(source, student)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


@transaction.atomic
def commit(report: ImportReport) -> list[Student]:
    """Write the rows that passed, all of them or none.

    One transaction for the whole batch: a constraint that fires on the last
    student -- because someone else enrolled that admission number while this
    file was on screen -- must not leave the first two hundred behind. The
    caller re-validates immediately before calling this, so the window is small,
    but "small" is not "closed" and a half-written roster is not recoverable by
    the person looking at it.
    """
    created: list[Student] = []
    for row in report.ready:
        assert row.record is not None  # guaranteed by RowResult.is_valid
        row.record.save()
        created.append(row.record)
    return created
