"""The contract between a school's spreadsheet and us, and the verdict on one.

The student import was the first of these and set the shape. Four entities now
share it -- students, classes, subjects and fee structures -- and this module is
the part none of them may disagree about.

Three decisions carried over from the student importer, because they are the
reason it works:

**The template is the contract.** A school fills in *our* columns. Nothing here
tries to work out the structure of an arbitrary document, because a guess about
which column held the amount is a guess about money. Columns are matched by
*name* rather than position, so a file with the columns reordered or a title row
above them still reads -- but the names are ours.

**Nothing is written until every row has been judged.** Parsing, validation and
writing are three separate passes, so the user sees the whole verdict before a
single ``INSERT``. Writing as we go and stopping at the first bad row leaves a
school with half a roster and no way to tell which half.

**A bad row is not a bad file.** Real spreadsheets have a typo in row 14. A row
that fails is reported with the field and the reason, and the rows that passed
can still go in. Failure is per row, and the report is the product.

Nothing in this module touches openpyxl -- :mod:`apps.core.spreadsheets` owns
that boundary and hands over plain strings. Keeping them apart means validation
can be tested without building a workbook, and means the same rows can be
re-validated straight from the session when the user confirms the import,
against a database that may have moved underneath them in the meantime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

#: How the template's guidance row announces itself. Half the people who
#: download a template will not delete that row, and a file that imports
#: "EXAMPLE" as a child is worse than one that refuses.
GUIDANCE_MARKER = "EXAMPLE"

#: A spreadsheet is a person's afternoon of typing, not a data feed. Well past
#: any real intake, and low enough that a runaway file is refused rather than
#: held in a session.
DEFAULT_MAX_ROWS = 2000

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalise_header(value: object) -> str:
    """Reduce a header cell -- or any name being matched -- to something comparable.

    ``"Parent / Guardian Phone "`` and ``"parent guardian phone"`` are the same
    column, and so is ``"Admission Number *"`` -- a template marks its required
    columns with an asterisk and gets its own file back.

    What it does *not* do is join what punctuation separated: ``"D.O.B."``
    reduces to ``"d o b"`` rather than to ``"dob"``, because collapsing them
    would also make ``"ID"`` and ``"I D"`` the same word as half the acronyms a
    school uses. A spelling like that is listed as an alias on the column, where
    it can be read.

    The same reduction resolves the *values* a school types into a column that
    names something else in the database: ``JSS 1A`` and ``jss 1 a`` are one
    class.
    """
    text = str(value if value is not None else "").strip().lower()
    return " ".join(_NON_ALNUM.sub(" ", text).split())


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    """One column of an import sheet.

    ``key`` is the field it feeds. ``label`` is what the template writes into
    the header row, and ``aliases`` are the other spellings a school's own
    spreadsheet is likely to use.
    """

    key: str
    label: str
    required: bool
    #: Written into the template's guidance row, under the header.
    example: str
    aliases: tuple[str, ...] = ()
    #: Roughly how wide the column should be in the generated template.
    width: int = 18


class Sheet:
    """A whole import sheet: its columns, its name, and how a row is described.

    One instance per entity, built at import time and treated as a constant.
    It is what the template writer, the file reader, the report and the review
    screen all read, so there is exactly one place that knows what a column is
    called -- the bug this replaces was four places drifting apart.
    """

    def __init__(
        self,
        *,
        name: str,
        noun: str,
        noun_plural: str,
        filename: str,
        columns: tuple[Column, ...],
        label_keys: tuple[str, ...] = (),
        guidance_column: str = "",
        max_rows: int = DEFAULT_MAX_ROWS,
        #: Enough recognised headers to be confident we found the header row
        #: rather than a row of data containing a word we know. Narrow sheets
        #: need a lower bar than the thirteen-column student one.
        header_match_threshold: int = 0,
        #: The class a row's verdict is built as. Subclassed where an entity's
        #: own screens have always called the record something -- the roster
        #: says ``row.student`` -- so that moving the machinery here did not
        #: have to rename it everywhere.
        result_class: type = None,
    ):
        self.name = name
        self.noun = noun
        self.noun_plural = noun_plural
        self.filename = filename
        self.columns = columns
        self.max_rows = max_rows
        #: The cells that name a row in the report, in the order they read.
        self.label_keys = label_keys or tuple(c.key for c in columns if c.required)[:2]
        #: Which column carries the guidance marker. The first one, unless the
        #: sheet says otherwise -- it has to be a column a school always fills,
        #: or the example row is not recognised on the way back in.
        self.guidance_column = guidance_column or columns[0].key
        self.result_class = result_class or RowResult

        self.by_key: dict[str, Column] = {c.key: c for c in columns}
        self.required_keys: tuple[str, ...] = tuple(
            c.key for c in columns if c.required
        )
        self.header_match_threshold = header_match_threshold or min(
            3, max(1, len(self.required_keys))
        )

        lookup: dict[str, str] = {}
        for column in columns:
            spellings = (column.label, column.key.replace("_", " "), *column.aliases)
            for spelling in spellings:
                lookup[normalise_header(spelling)] = column.key
        self._header_lookup = lookup

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Sheet {self.name!r} {len(self.columns)} columns>"

    def column_for_header(self, value: object) -> str | None:
        """The field a header cell names, or ``None`` if we do not recognise it."""
        return self._header_lookup.get(normalise_header(value))

    def label_for(self, column_key: str) -> str:
        """The heading a field is known by, for an error message."""
        column = self.by_key.get(column_key)
        return column.label if column else "This row"

    def guidance_for(self, column: Column) -> str:
        """What the template writes under ``column``'s heading.

        The example itself, except in the guidance column, which also carries
        the marker that makes the row recognisable on the way back in.
        """
        if column.key == self.guidance_column:
            return f"{GUIDANCE_MARKER}: {column.example} — this row is ignored"
        return column.example

    def describe(self, values: dict[str, str]) -> str:
        """How a row is named in the report -- its identifying cells, joined."""
        parts = [
            (values.get(key) or "").strip()
            for key in self.label_keys
        ]
        return " — ".join(part for part in parts if part) or "(blank row)"


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
    """The verdict on one row, and the record it would create.

    ``record`` is built but unsaved when the row passed and ``None`` when it
    did not, so :func:`commit` has nothing left to decide. ``extra`` carries
    whatever else a passing row resolved that the record cannot hold on its own
    -- the classes a subject is taught in, for instance, which cannot be set on
    an unsaved row.
    """

    sheet: Sheet
    number: int
    values: dict[str, str]
    errors: list[FieldError] = field(default_factory=list)
    record: object | None = None
    extra: dict = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def label(self) -> str:
        return self.sheet.describe(self.values)

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

    sheet: Sheet
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


class Judgement:
    """The errors accumulated against one row, and which fields are spoken for.

    Every importer ends by asking the model what *it* thinks -- phone format,
    field lengths, the born-after-admission check -- and none of them should
    restate a rule the model already holds. The trick that makes that work is
    excluding the fields this pass has already refused, so the user is not told
    twice about one cell in two different voices. ``refuse`` records both at
    once, which is the only reason the two lists cannot drift.
    """

    def __init__(self, sheet: Sheet):
        self.sheet = sheet
        self.errors: list[FieldError] = []
        self.refused: set[str] = set()

    def refuse(self, key: str, message: str) -> None:
        self.errors.append(FieldError(self.sheet.label_for(key), message))
        self.refused.add(key)

    def require(self, source: SourceRow) -> None:
        """Refuse every required column this row left empty."""
        for key in self.sheet.required_keys:
            if not source.get(key):
                self.refuse(key, "is required and this cell is empty.")

    def ask_the_model(self, instance, *, exclude: set[str] | None = None) -> None:
        """Add the model's own complaints, in the sheet's own column names.

        Runs even when the row has already failed, because the report exists to
        send the user back to their spreadsheet *once*. Finding the bad class
        today and the bad phone number on the next upload is the frustration
        this screen was built to remove.
        """
        skip = (exclude or set()) | self.refused
        try:
            instance.full_clean(
                exclude=skip,
                validate_unique=False,
                validate_constraints=False,
            )
        except ValidationError as exc:
            for key, messages in exc.message_dict.items():
                if key in skip:
                    continue
                for message in messages:
                    self.errors.append(
                        FieldError(self.sheet.label_for(key), f"— {message}")
                    )

    def verdict(self, source: SourceRow, record, **extra) -> RowResult:
        """The row's result: the record if it passed, nothing if it did not."""
        return self.sheet.result_class(
            sheet=self.sheet,
            number=source.number,
            values=dict(source.values),
            errors=self.errors,
            record=None if self.errors else record,
            extra={} if self.errors else extra,
        )


# ---------------------------------------------------------------------------
# Cell parsing
#
# Everything a spreadsheet does to a value on the way out, undone. These are
# repairs rather than validation: a leading zero Excel ate is not a mistake the
# school made and not one they can see.
# ---------------------------------------------------------------------------

#: Day first, because that is how the date is written on this continent and in
#: the spreadsheets these schools already keep. The templates ask for
#: YYYY-MM-DD, which is unambiguous whichever way you read it.
_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d",
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%d/%m/%y", "%d-%m-%y",
    "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y",
)


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


_AMOUNT_NOISE = re.compile(r"[\s, ₦]|NGN|N:", re.IGNORECASE)


def parse_amount(text: str) -> Decimal | None:
    """Money from a cell, or ``None`` if it cannot be read as money.

    A fee column is typed by a human and formatted by Excel, so it arrives as
    ``₦45,000``, ``45,000.00``, ``NGN 45000`` or ``45000`` in the same file.
    The currency mark, the thousands separators and the non-breaking spaces
    Excel inserts are all noise around one number.

    Parentheses mean negative in accounting notation; they are stripped to a
    minus sign rather than silently dropped, so a negative amount is *refused*
    by the caller instead of becoming a positive one.
    """
    text = (text or "").strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    cleaned = _AMOUNT_NOISE.sub("", text)
    if not cleaned or cleaned in {"-", "+", "."}:
        return None
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return -amount if negative else amount


#: Both separators, because a school listing several things in one cell will use
#: whichever is to hand. Neither appears inside a class or subject name.
_LIST_SPLIT = re.compile(r"[;,/|]+")


def parse_list(text: str) -> list[str]:
    """One cell holding several values, split and tidied.

    Used where a row genuinely has a list in it -- the classes a subject is
    taught in -- which is the one place a fixed template cannot give each value
    a column of its own, because the number of them differs per school.
    """
    return [part.strip() for part in _LIST_SPLIT.split(text or "") if part.strip()]
