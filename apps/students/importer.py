"""Validation for the Excel student import.

The bulk half of step 4. A school arriving with three hundred students already
on the roll is not going to type them in one at a time, and the spreadsheet they
already keep is the closest thing they have to a database.

Two decisions shape everything here:

**Nothing is written until every row has been judged.** Parsing, validation and
writing are three separate passes, so the user sees the whole verdict before a
single ``INSERT`` happens. The alternative -- writing as we go and stopping at
the first bad row -- leaves the school with a half-imported roster and no way to
tell which half.

**A bad row is not a bad file.** Real spreadsheets have a typo in row 14 and a
class name that was renamed last term. All-or-nothing would send the user back
to fix one cell at a time, so a row that fails is reported with the field and
the reason, and the rows that passed can still go in. Failure is per row, and
the report is the product.

This module never touches openpyxl -- :mod:`apps.students.workbook` owns that
boundary and hands over plain strings. Keeping them apart means validation can
be tested without building a workbook, and means the same rows can be
re-validated straight from the session when the user confirms the import,
against a database that may have moved underneath them in the meantime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.academics.models import Class

from .models import Sex, Student, StudentStatus
from .validators import normalise_admission_number, normalise_phone


@dataclass(frozen=True)
class Column:
    """One column of the import sheet.

    ``key`` is the model field it feeds. ``label`` is what the template writes
    into the header row, and ``aliases`` are the other spellings a school's own
    spreadsheet is likely to use -- columns are matched by name, never by
    position, so a file with the columns in a different order still imports.
    """

    key: str
    label: str
    required: bool
    #: Written into the template's guidance row, under the header.
    example: str
    aliases: tuple[str, ...] = ()
    #: Roughly how wide the column should be in the generated template.
    width: int = 18


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
           ("dob", "birth date", "birthdate", "date born"), width=24),
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

COLUMNS_BY_KEY: dict[str, Column] = {column.key: column for column in COLUMNS}

REQUIRED_KEYS: tuple[str, ...] = tuple(c.key for c in COLUMNS if c.required)

#: How the template's guidance row announces itself, and the column it says it
#: in. Half the people who download the template will not delete that row, and
#: a file that imports "EXAMPLE" as a child is worse than one that refuses.
GUIDANCE_MARKER = "EXAMPLE"
GUIDANCE_COLUMN = "admission_number"


def guidance_for(column: Column) -> str:
    """What the template writes under ``column``'s heading.

    The example itself, except in :data:`GUIDANCE_COLUMN`, which also carries
    the marker that makes the row recognisable on the way back in.
    """
    if column.key == GUIDANCE_COLUMN:
        return f"{GUIDANCE_MARKER}: {column.example} — this row is ignored"
    return column.example

#: A spreadsheet is a person's afternoon of typing, not a data feed. Well past
#: any real intake, and low enough that a runaway file is refused rather than
#: held in a session.
MAX_ROWS = 2000

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalise_header(value: object) -> str:
    """Reduce a header cell to something matchable.

    ``"Parent / Guardian Phone "`` and ``"parent guardian phone"`` are the same
    column; so are ``"D.O.B."`` and ``"dob"``, and so is ``"Admission Number *"``
    -- the template marks its required columns with an asterisk and gets its own
    file back.
    """
    text = str(value if value is not None else "").strip().lower()
    return " ".join(_NON_ALNUM.sub(" ", text).split())


#: Every spelling of a header we accept, normalised, pointing at the field it
#: fills. Built from the labels the template writes plus the names a school's
#: own spreadsheet is likely to already use.
_HEADER_LOOKUP: dict[str, str] = {}
for _column in COLUMNS:
    for _spelling in (_column.label, _column.key.replace("_", " "), *_column.aliases):
        _HEADER_LOOKUP[normalise_header(_spelling)] = _column.key


def column_for_header(value: object) -> str | None:
    """The field a header cell names, or ``None`` if we do not recognise it."""
    return _HEADER_LOOKUP.get(normalise_header(value))


# ---------------------------------------------------------------------------
# What a parsed sheet looks like
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRow:
    """One data row as it was read: the spreadsheet's own row number, and text.

    Values are strings by the time they reach here -- dates included, as ISO --
    so a parsed sheet survives a round trip through the session between the
    validation screen and the user confirming it.
    """

    number: int
    values: dict[str, str]

    def get(self, key: str) -> str:
        return (self.values.get(key) or "").strip()

    @property
    def is_blank(self) -> bool:
        return not any(str(v).strip() for v in self.values.values())

    def as_dict(self) -> dict:
        return {"number": self.number, "values": dict(self.values)}

    @classmethod
    def from_dict(cls, payload: dict) -> "SourceRow":
        return cls(number=int(payload["number"]), values=dict(payload["values"]))


# ---------------------------------------------------------------------------
# What a validated sheet looks like
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldError:
    """One reason one cell was refused, named the way the sheet names it."""

    column: str
    message: str


@dataclass
class RowResult:
    """The verdict on one row, and the student it would create."""

    number: int
    values: dict[str, str]
    errors: list[FieldError] = field(default_factory=list)
    #: Built but unsaved when the row passed; ``None`` when it did not.
    student: Student | None = None

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def label(self) -> str:
        """How the row is named in the report -- adm. no. and name if we have them."""
        number = (self.values.get("admission_number") or "").strip()
        name = " ".join(
            part for part in (
                (self.values.get("first_name") or "").strip(),
                (self.values.get("last_name") or "").strip(),
            ) if part
        )
        return " — ".join(part for part in (number, name) if part) or "(blank row)"

    @property
    def summary(self) -> str:
        """One line, the way a person would read it out.

        ``Row 14: Admission Number 'FA/023' already belongs to ...; Class ...``
        """
        detail = "; ".join(f"{e.column} {e.message}" for e in self.errors)
        return f"Row {self.number}: {detail}"


@dataclass
class ImportReport:
    """Every row's verdict, plus what the file as a whole looked like."""

    rows: list[RowResult] = field(default_factory=list)
    #: Headers in the uploaded file we had no field for. Not an error -- a
    #: school's own sheet carries columns we do not want -- but worth saying.
    unknown_columns: list[str] = field(default_factory=list)
    #: Optional columns the file simply did not have.
    absent_columns: list[str] = field(default_factory=list)
    sheet_name: str = ""

    @property
    def ready(self) -> list[RowResult]:
        return [row for row in self.rows if row.is_valid]

    @property
    def failed(self) -> list[RowResult]:
        return [row for row in self.rows if not row.is_valid]

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def ready_count(self) -> int:
        return len(self.ready)

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    @property
    def has_anything_to_import(self) -> bool:
        return bool(self.ready)


# ---------------------------------------------------------------------------
# Cell parsing
# ---------------------------------------------------------------------------

#: Day first, because that is how the date is written on this continent and in
#: the spreadsheets these schools already keep. The template asks for
#: YYYY-MM-DD, which is unambiguous whichever way you read it.
_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d",
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%d/%m/%y", "%d-%m-%y",
    "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y",
)

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


def parse_date(text: str) -> date | None:
    """A date from any of the shapes a spreadsheet writes one in, or ``None``."""
    text = text.strip()
    if not text:
        return None
    # A cell Excel already knows is a date arrives as an ISO string from the
    # reader; the rest is whatever the typist felt like.
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


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


class ClassIndex:
    """The branch's classes, looked up the way a spreadsheet spells them.

    A school types ``JSS 1A``, ``JSS 1 A`` or just ``JSS 1``; all three should
    find the class. What must *not* happen is a silent guess when the name is
    genuinely ambiguous -- ``Primary 1`` where the branch runs ``Primary 1A``
    and ``Primary 1B`` is a question for the user, not for us.
    """

    def __init__(self, branch):
        self.branch = branch
        self._active: dict[str, list[Class]] = {}
        self._inactive: set[str] = set()

        for klass in Class.objects.filter(branch=branch):
            keys = {
                normalise_header(klass.display_name),
                normalise_header(f"{klass.name} {klass.stream}"),
                normalise_header(klass.name),
            }
            for key in keys:
                if not key:
                    continue
                if klass.is_active:
                    self._active.setdefault(key, []).append(klass)
                else:
                    self._inactive.add(key)

    def resolve(self, text: str) -> tuple[Class | None, str | None]:
        """Return ``(class, error message)``; exactly one of the two is set."""
        key = normalise_header(text)
        matches = self._active.get(key, [])
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            names = ", ".join(sorted(k.display_name for k in matches))
            return None, (
                f"'{text}' matches more than one class ({names}). "
                f"Include the arm, e.g. '{matches[0].display_name}'."
            )
        if key in self._inactive:
            return None, (
                f"'{text}' is a class at {self.branch.name} but it is no longer "
                f"active. Reactivate it, or use a different class."
            )
        return None, (
            f"'{text}' is not a class at {self.branch.name}. Check the spelling, "
            f"or set the class up first."
        )

    @property
    def names(self) -> list[str]:
        """Every active class name, for the template's reference sheet."""
        seen = {
            klass.pk: klass
            for matches in self._active.values() for klass in matches
        }
        return [klass.display_name for klass in sorted(
            seen.values(),
            key=lambda k: (k.level, k.year_in_level, k.stream, k.name),
        )]


def validate(rows, *, branch, sheet_name: str = "", unknown_columns=None,
             absent_columns=None) -> ImportReport:
    """Judge every row independently and return the report.

    Nothing is written. The ``Student`` built for a passing row is left unsaved
    on its :class:`RowResult` so that :func:`commit` has nothing left to decide.

    Two queries regardless of file size: one for the branch's classes, one for
    the admission numbers already in use there.
    """
    report = ImportReport(
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
    result = RowResult(number=source.number, values=dict(source.values))
    errors: list[FieldError] = []
    #: Fields already spoken for, so the model does not say the same thing
    #: again in its own words.
    refused: set[str] = set()

    def refuse(key: str, message: str) -> None:
        errors.append(FieldError(COLUMNS_BY_KEY[key].label, message))
        refused.add(key)

    # -- required text -------------------------------------------------------
    for key in REQUIRED_KEYS:
        if not source.get(key):
            refuse(key, "is required and this cell is empty.")

    # -- admission number ----------------------------------------------------
    admission_number = normalise_admission_number(source.get("admission_number"))
    if admission_number:
        folded = admission_number.upper()
        first_seen = seen_in_file.get(folded)
        if first_seen is not None:
            refuse(
                "admission_number",
                f"'{admission_number}' is already used on row {first_seen} of "
                f"this file.",
            )
        elif folded in taken:
            refuse(
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
            refuse("school_class", problem)

    # -- sex -----------------------------------------------------------------
    sex_text = source.get("sex")
    sex = ""
    if sex_text:
        matched = _SEX_WORDS.get(normalise_header(sex_text))
        if matched is None:
            refuse("sex", f"'{sex_text}' is not a sex. Use Male or Female.")
        else:
            sex = matched

    # -- status --------------------------------------------------------------
    status_text = source.get("status")
    status = StudentStatus.ACTIVE
    if status_text:
        matched = _STATUS_WORDS.get(normalise_header(status_text))
        if matched is None:
            refuse(
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
            refuse(
                key,
                f"'{text}' could not be read as a date. Use YYYY-MM-DD, "
                f"e.g. 2019-04-12.",
            )
        dates[key] = parsed

    # -- the model's own rules ------------------------------------------------
    # Everything above was parsing; from here the student is real enough to ask
    # the model what it thinks. Phone format, email, name lengths and the
    # born-after-admission check all live there already and are not restated.
    #
    # This runs even when the row has already failed, because the report exists
    # to send the user back to their spreadsheet *once*. Finding the bad class
    # today and the bad phone number on the next upload is the frustration this
    # screen was built to remove.
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

    try:
        student.full_clean(
            # Parsed and reported above; the uniqueness check in particular is
            # deliberately ours, because it has to see the other rows of this
            # file as well as the roster already in the database.
            exclude={"school", "branch", "school_class", "sex", "status"} | refused,
            validate_unique=False,
            validate_constraints=False,
        )
    except ValidationError as exc:
        for key, messages in exc.message_dict.items():
            if key in refused:
                continue
            column = COLUMNS_BY_KEY.get(key)
            label = column.label if column else "This row"
            for message in messages:
                errors.append(FieldError(label, f"— {message}"))

    result.errors = errors
    result.student = None if errors else student
    return result


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
        assert row.student is not None  # guaranteed by RowResult.is_valid
        row.student.save()
        created.append(row.student)
    return created
