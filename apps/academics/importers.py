"""Validation for the class and subject imports.

Two sheets, one module, because they share the ladder they are both described
against. Everything about *how* an import works -- the column contract, the
per-row verdict, the report, the three screens -- is in
:mod:`apps.core.imports`, :mod:`apps.core.spreadsheets` and
:mod:`apps.core.import_flow`, and the reasoning behind it is written up there.
What is here is only what is true of classes and subjects.

Classes: why Level and Year are optional
----------------------------------------
A school that runs Grade 1 to Grade 12, or Basic 1 to Basic 9, does not know
about our four bands and should not have to. So the Level and Year columns may
be left blank and are read off the name -- ``apps.academics.curriculum`` already
knows that Basic 7 is junior secondary and that "Primary 4" is year four, because
the bulk-add screen needed the same thing.

What it must *not* do is guess. A name with no band in it ("Reception", "Alpha
Class") is a question for the school, so the row is refused with the four band
names in the message rather than filed under Nursery and forgotten about.

Subjects: why "Taught In" is one cell
-------------------------------------
A subject genuinely spans several classes -- Mathematics runs from Primary 1 to
SSS 3 -- and the number of them differs per school, so no fixed set of columns
can express it. One cell holding a list is the only shape a *fixed* template can
take here, and it accepts a level name as well as a class name because "Primary"
is how a school says the six primary classes.
"""

from __future__ import annotations

from django.db import transaction

from apps.core.imports import (
    Column,
    ImportReport,
    Judgement,
    Sheet,
    SourceRow,
    normalise_header,
    parse_list,
)

from .curriculum import infer_level, infer_year
from .lookup import ClassIndex, level_for, level_names
from .models import Class, Level, Subject

# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------

CLASS_COLUMNS: tuple[Column, ...] = (
    Column(
        "name", "Class", True,
        "Grade 1 — the class without its arm",
        ("class", "class name", "classroom", "grade", "form", "level name"),
        width=40,
    ),
    Column(
        "stream", "Stream / Arm", False,
        "Science, Arts, A or B — blank if the year is one group",
        ("stream", "arm", "section", "suffix"),
        width=46,
    ),
    Column(
        "level", "Level", False,
        "Nursery, Primary, Junior Secondary or Senior Secondary — "
        "worked out from the class name if left blank",
        ("band", "school level", "category", "department"),
        width=56,
    ),
    Column(
        "year_in_level", "Year", False,
        "4 for Primary 4 — worked out from the class name if left blank",
        ("year", "year in level", "class year", "position"),
        width=48,
    ),
)

CLASS_SHEET = Sheet(
    name="Classes",
    noun="class",
    noun_plural="classes",
    filename="schoolcord-class-import.xlsx",
    columns=CLASS_COLUMNS,
    label_keys=("name", "stream"),
    guidance_column="name",
    # One required column, so the usual bar of three recognised headings cannot
    # be met by a valid file.
    header_match_threshold=1,
)

#: The widest a year can sensibly be. Beyond this the cell is not a year -- it
#: is a year of entry, or an admission number, in the wrong column.
MAX_YEAR = 20


def validate_classes(rows, *, branch, sheet_name: str = "", unknown_columns=None,
                     absent_columns=None) -> ImportReport:
    """Judge every class row independently and return the report.

    One query regardless of file size: the classes already at the branch.
    """
    report = ImportReport(
        sheet=CLASS_SHEET,
        unknown_columns=list(unknown_columns or []),
        absent_columns=list(absent_columns or []),
        sheet_name=sheet_name,
    )

    # Keyed on the name and arm as a person reads them, not as they are stored:
    # "Primary 1" and "primary 1" are the same class to a school, and letting
    # the second one through would give them two.
    taken: dict[tuple[str, str], Class] = {
        (normalise_header(k.name), normalise_header(k.stream)): k
        for k in Class.objects.filter(branch=branch)
    }
    seen_in_file: dict[tuple[str, str], int] = {}

    for source in rows:
        report.rows.append(_validate_class(source, branch, taken, seen_in_file))
    return report


def _validate_class(source: SourceRow, branch, taken, seen_in_file):
    judge = Judgement(CLASS_SHEET)
    judge.require(source)

    name = source.get("name")
    stream = source.get("stream")

    # -- already there, here or in the file ----------------------------------
    if name:
        key = (normalise_header(name), normalise_header(stream))
        first_seen = seen_in_file.get(key)
        existing = taken.get(key)
        if first_seen is not None:
            judge.refuse(
                "name",
                f"'{_spoken(name, stream)}' is already on row {first_seen} of "
                f"this file.",
            )
        elif existing is not None:
            judge.refuse(
                "name",
                f"'{existing.display_name}' is already a class at "
                f"{branch.name}. Rename it on the Classes screen rather than "
                f"importing it again.",
            )
        else:
            # Claimed even when the row fails elsewhere, so a file listing the
            # same class twice blames the second one.
            seen_in_file[key] = source.number

    # -- level, given or read off the name -----------------------------------
    level = None
    level_text = source.get("level")
    if level_text:
        level = level_for(level_text)
        if level is None:
            judge.refuse(
                "level",
                f"'{level_text}' is not a level. Use one of: "
                f"{', '.join(level_names())}.",
            )
    elif name:
        level = infer_level(name)
        if level is None:
            judge.refuse(
                "level",
                f"could not be worked out from '{name}'. Fill the Level column "
                f"in with one of: {', '.join(level_names())}.",
            )

    # -- year, given or read off the name ------------------------------------
    year = None
    year_text = source.get("year_in_level")
    if year_text:
        try:
            year = int(float(year_text))
        except ValueError:
            judge.refuse(
                "year_in_level",
                f"'{year_text}' is not a number. Use the position of this class "
                f"inside its level — 4 for Primary 4.",
            )
        else:
            if not 0 <= year <= MAX_YEAR:
                judge.refuse(
                    "year_in_level",
                    f"'{year_text}' is not a year within a level. Use a number "
                    f"from 0 to {MAX_YEAR} — 4 for Primary 4.",
                )
                year = None
    elif name:
        # No number in the name ("Reception", "Pre-KG") is not an error: the
        # ordering default puts it first in its band, which is where a school
        # that names a class without a number means it to be.
        year = infer_year(name)

    klass = Class(
        school_id=branch.school_id,
        branch=branch,
        name=name,
        stream=stream,
        level=level if level is not None else Level.PRIMARY,
        year_in_level=year if year is not None else 1,
        is_active=True,
    )
    judge.ask_the_model(
        klass,
        # Level and year were parsed and reported above; branch and school are
        # ours, not the sheet's.
        exclude={"school", "branch", "level", "year_in_level"},
    )
    return judge.verdict(source, klass)


def _spoken(name: str, stream: str) -> str:
    """The class as staff would say it, for a message about a row not yet saved."""
    if not stream:
        return name
    return f"{name}{stream}" if len(stream) == 1 else f"{name} {stream}"


@transaction.atomic
def commit_classes(report: ImportReport) -> list[Class]:
    """Write the class rows that passed, all of them or none.

    One transaction for the whole batch: nineteen classes half created, with no
    indication of which nine, is a worse state to hand back than nothing at all
    -- the same reason the bulk-add screen is atomic.
    """
    created: list[Class] = []
    for row in report.ready:
        row.record.save()
        created.append(row.record)
    return created


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------

SUBJECT_COLUMNS: tuple[Column, ...] = (
    Column(
        "name", "Subject", True, "Mathematics",
        ("subject", "subject name", "title"), width=32,
    ),
    Column(
        "code", "Code", False, "MTH — optional short code",
        ("subject code", "short code", "abbreviation", "abbr"), width=30,
    ),
    Column(
        "classes", "Taught In", False,
        "Primary; JSS 1; JSS 2 — class names or a level, separated by "
        "semicolons. Write All for every class, or leave blank to assign later.",
        ("taught in", "class", "classes", "taught", "applies to", "level",
         "assigned classes"),
        width=64,
    ),
)

SUBJECT_SHEET = Sheet(
    name="Subjects",
    noun="subject",
    noun_plural="subjects",
    filename="schoolcord-subject-import.xlsx",
    columns=SUBJECT_COLUMNS,
    label_keys=("name", "code"),
    guidance_column="name",
    header_match_threshold=1,
)


def validate_subjects(rows, *, branch, sheet_name: str = "", unknown_columns=None,
                      absent_columns=None) -> ImportReport:
    """Judge every subject row independently and return the report.

    Two queries regardless of file size: the branch's classes, and the subjects
    already taught there.
    """
    report = ImportReport(
        sheet=SUBJECT_SHEET,
        unknown_columns=list(unknown_columns or []),
        absent_columns=list(absent_columns or []),
        sheet_name=sheet_name,
    )
    classes = ClassIndex(branch)
    taken: dict[str, str] = {
        normalise_header(name): name
        for name in Subject.objects.filter(branch=branch).values_list(
            "name", flat=True
        )
    }
    seen_in_file: dict[str, int] = {}

    for source in rows:
        report.rows.append(
            _validate_subject(source, branch, classes, taken, seen_in_file)
        )
    return report


def _validate_subject(source: SourceRow, branch, classes: ClassIndex, taken,
                      seen_in_file):
    judge = Judgement(SUBJECT_SHEET)
    judge.require(source)

    name = source.get("name")
    if name:
        key = normalise_header(name)
        first_seen = seen_in_file.get(key)
        if first_seen is not None:
            judge.refuse(
                "name",
                f"'{name}' is already on row {first_seen} of this file.",
            )
        elif key in taken:
            judge.refuse(
                "name",
                f"'{taken[key]}' is already a subject at {branch.name}. Edit it "
                f"to change the classes it is taught in.",
            )
        else:
            seen_in_file[key] = source.number

    # -- the classes it is taught in -----------------------------------------
    # Blank is allowed and normal: a school importing its subject list before
    # its class list has not made a mistake, and the subjects screen already
    # counts the ones not yet assigned.
    taught_in: list[Class] = []
    cell = source.get("classes")
    if cell:
        taught_in, problems = classes.resolve_many(parse_list(cell))
        # Every unresolved name, not just the first: a school sent back to fix
        # one typo per upload is the frustration this screen exists to remove.
        for problem in problems:
            judge.refuse("classes", problem)

    subject = Subject(
        school_id=branch.school_id,
        branch=branch,
        name=name,
        code=source.get("code"),
        is_active=True,
    )
    judge.ask_the_model(subject, exclude={"school", "branch", "classes"})
    # The classes ride on the result rather than the record: a many-to-many
    # cannot be set on an unsaved row, so commit() does it once the subject has
    # a primary key.
    return judge.verdict(source, subject, classes=taught_in)


@transaction.atomic
def commit_subjects(report: ImportReport) -> list[Subject]:
    """Write the subject rows that passed, and attach their classes."""
    created: list[Subject] = []
    for row in report.ready:
        subject = row.record
        subject.save()
        chosen = row.extra.get("classes") or []
        if chosen:
            subject.classes.set(chosen)
        created.append(subject)
    return created
