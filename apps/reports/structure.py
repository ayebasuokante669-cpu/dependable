"""What a report *is*, before anyone decides how to draw it.

A report on this platform has two renderers: the page at ``/reports/`` and the
PDF behind its download button. They must never disagree -- a printed figure
that differs from the one on screen is worse than no export at all -- so
neither of them computes anything. :mod:`apps.reports.reporting` builds one of
these structures, and both renderers walk it.

Everything here is display-ready and immutable. A :class:`Cell` carries the
figure already formatted, in both spellings it needs:

* ``text`` -- what the page shows, money included: ``₦102,000``;
* ``printed`` -- what the PDF prints: ``NGN 102,000``.

They differ for exactly one reason, explained on :func:`printed_amount`.

Nothing in this module imports ReportLab or a Django template. That is the
point: the shape is the contract, and a third renderer -- a spreadsheet, an
emailed summary -- would need no changes here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from apps.core.templatetags.charts import percent_label, percent_of
from apps.core.templatetags.money import naira

#: Column alignment. Two values, because a table of money needs no more.
LEFT = "left"
RIGHT = "right"


def printed_amount(value) -> str:
    """The same figure :func:`naira` formats, spelled for a PDF.

    The fourteen fonts every PDF reader has built in are Latin-1, and the naira
    sign is not in Latin-1. ReportLab does not refuse a character it cannot
    encode -- it quietly substitutes ZapfDingbats -- so the sign comes out as a
    dingbat, on every amount, with no error anywhere to say so.

    The fix is deliberately not to embed a font: that is a file the school then
    has to keep shipped and licensed, for one character. The report prints the
    ISO code instead, which is what a bank statement does too.
    """
    if value is None or value == "":
        return "—"
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return "—"
    if amount == amount.to_integral_value():
        return f"NGN {amount:,.0f}"
    return f"NGN {amount:,.2f}"


@dataclass(frozen=True)
class Cell:
    """One figure in a table, or one number beside a label.

    Built through the classmethods rather than the constructor, so "this is
    money" is decided once -- where the figure comes out of the derivation --
    and not again in each renderer.
    """

    text: str
    #: Set only where the print spelling differs from the screen one.
    plain: str = ""
    #: ``0``..``100`` with two decimals, when this cell is drawn as a bar.
    share: str = ""
    #: A ``status-pill`` class, when the cell is a state rather than a figure.
    pill: str = ""
    #: Where the page links this cell. The PDF ignores it: a printed report is
    #: read on paper, and an underlined link on paper leads nowhere.
    href: str = ""
    strong: bool = False
    muted: bool = False

    @property
    def printed(self) -> str:
        return self.plain or self.text

    @classmethod
    def of(cls, text, **kwargs) -> "Cell":
        return cls(text="" if text is None else str(text), **kwargs)

    @classmethod
    def money(cls, amount, **kwargs) -> "Cell":
        return cls(text=naira(amount), plain=printed_amount(amount), **kwargs)

    @classmethod
    def count(cls, value, **kwargs) -> "Cell":
        return cls(text=f"{value:,}", **kwargs)

    @classmethod
    def share_of(cls, part, whole, **kwargs) -> "Cell":
        """A proportion, as a whole-number percentage.

        An em dash when there is no whole to be a share of, which is honest
        where ``0%`` would not be.
        """
        return cls(text=percent_label(part, whole), **kwargs)

    @classmethod
    def blank(cls) -> "Cell":
        return cls(text="—", muted=True)


def prose(text: str, **amounts) -> Cell:
    """A sentence, in both spellings, for the places a report explains itself.

    Table footnotes and the notes at the end of a report are printed as well as
    shown, and a sentence with an amount in it hits exactly the problem
    :func:`printed_amount` describes -- so it needs the same two spellings a
    figure does, and gets them from the same :class:`Cell`.

    ``text`` carries ``{name}`` placeholders and each keyword is an amount::

        prose("{owed} still owed by {n} families.", owed=total)

    A sentence with no amounts in it is just as welcome; it comes back with one
    spelling used twice, which is the truth about it.
    """
    screen = text.format(**{key: naira(value) for key, value in amounts.items()})
    printed = text.format(
        **{key: printed_amount(value) for key, value in amounts.items()}
    )
    return Cell(text=screen, plain=printed)


@dataclass(frozen=True)
class Column:
    """One column of a table, and how both renderers should treat it."""

    label: str
    align: str = LEFT
    #: Tabular figures on screen, right-aligned in the PDF.
    numeric: bool = False
    #: Drawn as a share bar rather than as text. Its header is a screen-reader
    #: label only, and the PDF leaves the column out: the figure it draws is
    #: already in the column beside it, and a bar is decoration for an eye
    #: scanning a screen.
    bar: bool = False
    #: Relative width. Used only by the PDF, which has a fixed grid to fill.
    weight: float = 1.0


@dataclass(frozen=True)
class Filled:
    """A cell paired with the column it sits in.

    A row is stored as bare cells in column order, which is the natural way to
    build one. A renderer walking a row needs the column too -- is this a bar, is
    it right-aligned -- and a Django template cannot index one list by another's
    loop counter without a filter invented for the purpose. So the pairing
    happens here instead, once, in the place that knows both.
    """

    column: "Column"
    cell: Cell


@dataclass(frozen=True)
class Table:
    """A titled grid. ``rows`` are cells in column order."""

    key: str
    title: str
    columns: tuple[Column, ...]
    rows: tuple[tuple[Cell, ...], ...] = ()
    #: A footing row, cells in column order. Empty means no total line.
    total: tuple[Cell, ...] = ()
    #: The footnote that keeps the grid from being misread. Built with
    #: :func:`prose`, because it is printed as well as shown.
    note: Cell | None = None
    empty: str = "Nothing to report for this period yet."
    #: ``(label, href)`` for a link beside the title, already reversed. Screen
    #: only: an underlined link on paper leads nowhere.
    link: tuple[str, str] | None = None

    @property
    def printable_columns(self) -> list[int]:
        """Indexes of the columns the PDF prints -- everything but the bars."""
        return [i for i, column in enumerate(self.columns) if not column.bar]

    @property
    def filled_rows(self) -> tuple[tuple[Filled, ...], ...]:
        return tuple(self._filled(row) for row in self.rows)

    @property
    def filled_total(self) -> tuple[Filled, ...]:
        return self._filled(self.total) if self.total else ()

    def _filled(self, row) -> tuple[Filled, ...]:
        return tuple(
            Filled(column=column, cell=cell)
            for column, cell in zip(self.columns, row)
        )


@dataclass(frozen=True)
class Segment:
    """One part of a whole: a slice of a bar, and a line of its legend.

    ``key`` is a design-system colour token -- ``paid``, ``partial``,
    ``unpaid``, ``overdue``, ``brand`` -- never a colour. A chart that named its
    own green would be the one place on the platform where green stopped
    meaning "settled".
    """

    key: str
    label: str
    value: Cell
    share: str = "0"
    share_label: str = ""
    #: Legend-only. "Still to come" is a real line of the legend and not a drawn
    #: segment: it is the empty part of the track.
    in_bar: bool = True


@dataclass(frozen=True)
class Chart:
    """A stacked bar with a labelled legend.

    One shape for both charts on the report, because they are the same picture
    of two different wholes: money against what was expected, and students
    across the four payment states.

    Colour is never the only signal -- every segment is named and counted in the
    legend, which doubles as the table view of the same figures. That rule is
    not decoration: the amber/green pair in the status palette separates by only
    a few units of perceptual distance under protanopia, so identity has to rest
    on the label.
    """

    key: str
    title: str
    #: The one figure the chart exists to say. Empty for a chart whose legend
    #: carries all of it.
    headline: str = ""
    caption: str = ""
    segments: tuple[Segment, ...] = ()
    #: How many legend columns to lay out on a wide screen.
    legend_columns: int = 2

    @property
    def has_data(self) -> bool:
        return any(segment.share not in ("", "0") for segment in self.segments)


@dataclass(frozen=True)
class Stat:
    """A headline figure, with the sentence that stops it being misread."""

    label: str
    value: Cell
    note: str = ""
    #: A payment-status token, or ``brand``. Colours the figure, nothing else.
    tone: str = "brand"
    href: str = ""


@dataclass(frozen=True)
class Report:
    """One finished report, ready for either renderer."""

    key: str
    title: str
    #: Which school and campus this covers, spelled out -- the first thing
    #: anyone checks on a printed report they were handed.
    scope_label: str
    #: Which term, or terms, the figures are measured against.
    period_label: str
    generated_at: datetime
    prepared_for: str = ""
    stats: tuple[Stat, ...] = ()
    charts: tuple[Chart, ...] = ()
    tables: tuple[Table, ...] = ()
    #: The sentences that keep the figures honest: what is counted, what is not,
    #: and what is missing. Printed as well as shown, so they are built with
    #: :func:`prose`.
    notes: tuple[Cell, ...] = ()

    @property
    def filename(self) -> str:
        """A filename that says what the file is without being opened."""
        stamp = self.generated_at.strftime("%Y-%m-%d")
        return f"{_slug(self.scope_label) or 'report'}-collections-{stamp}.pdf"


def _slug(text: str) -> str:
    slug = "".join(c.lower() if c.isalnum() else "-" for c in text)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:60]


def bar_share(part, whole) -> str:
    """The width for a bar, as a bare two-decimal number.

    The same filter the dashboards' CSS bars use, so a bar here and a bar there
    drawn from the same two figures come out the same length.
    """
    return percent_of(part, whole)


def bar_label(part, whole) -> str:
    return percent_label(part, whole)
