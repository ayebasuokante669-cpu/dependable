"""The ReportLab boundary: a :class:`~apps.reports.structure.Report` as a PDF.

Everything in this module knows about paper and nothing about school fees. It is
handed a finished report -- every figure already derived, grouped and formatted
by :mod:`apps.reports.reporting` -- and decides only where things go on the page.
It computes no totals, resolves no querysets and reads no models. That is what
makes it safe for the page and the download to agree: they are two renderers of
one structure, not two ways of working the same thing out.

**Why a real PDF here, when the payment receipt deliberately is not one.**
``ReceiptView`` prints from the browser, on the grounds that a browser already
knows how to print one page and a PDF library would be a dependency the school
has to keep working. That reasoning holds for one page and stops holding here. A
report is a document somebody files, emails to a bank, or hands to a board: it
needs a fixed page size, repeating table headers, page numbers and a filename,
and none of those survive "Ctrl-P, save as PDF" intact across four browsers. So
this one dependency is worth it, and it is confined to this file -- nothing else
in the codebase imports ReportLab.

**The colours are the app's own tokens**, copied here as hex because a PDF has no
CSS custom properties. They are the light-theme values: paper is white, so a
report printed from a school running dark mode still comes out readable. The
four payment states keep the four colours they have everywhere else, and every
segment drawn is also named in a legend -- on paper as on screen, hue is never
the only signal.
"""

from __future__ import annotations

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from apps.core.branding import PRODUCT_NAME

from .structure import RIGHT

# ---------------------------------------------------------------------------
# The palette, from assets/app.css (@theme, light values)
# ---------------------------------------------------------------------------

BRAND_900 = colors.HexColor("#0f2547")
BRAND_700 = colors.HexColor("#1d3f79")
BRAND_500 = colors.HexColor("#3566b3")
BRAND_50 = colors.HexColor("#eef3fb")
INK_900 = colors.HexColor("#1c2432")
INK_700 = colors.HexColor("#435065")
INK_500 = colors.HexColor("#626e88")
INK_400 = colors.HexColor("#8290a8")
INK_200 = colors.HexColor("#dfe4ed")
INK_100 = colors.HexColor("#eef1f6")

#: The four payment states, plus the two extra keys the collection chart uses.
#: Same tokens, same meanings, same colours as the screens.
STATE_COLOURS = {
    "paid": colors.HexColor("#0f7a52"),
    "partial": colors.HexColor("#a86a09"),
    "unpaid": colors.HexColor("#5a6b85"),
    "overdue": colors.HexColor("#b32424"),
    "brand": BRAND_500,
    # "Still to come" is the empty part of a track, so it is the track's colour.
    "still": INK_200,
}

PAGE_SIZE = A4
MARGIN = 14 * mm
CONTENT_WIDTH = PAGE_SIZE[0] - 2 * MARGIN


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _style(name: str, **kwargs) -> ParagraphStyle:
    base = {
        "fontName": "Helvetica",
        "fontSize": 8.5,
        "leading": 11.5,
        "textColor": INK_700,
    }
    base.update(kwargs)
    return ParagraphStyle(name, **base)


TITLE = _style("title", fontName="Helvetica-Bold", fontSize=19, leading=23,
               textColor=BRAND_900)
SUBTITLE = _style("subtitle", fontSize=10.5, leading=14, textColor=INK_700)
META = _style("meta", fontSize=8, leading=11, textColor=INK_500)
SECTION = _style("section", fontName="Helvetica-Bold", fontSize=11.5, leading=15,
                 textColor=BRAND_900, spaceBefore=2, spaceAfter=4)
NOTE = _style("note", fontSize=7.5, leading=10.5, textColor=INK_500)
EMPTY = _style("empty", fontSize=8.5, leading=12, textColor=INK_500)

STAT_LABEL = _style("stat-label", fontSize=7.5, leading=9.5, textColor=INK_500)
STAT_VALUE = _style("stat-value", fontName="Helvetica-Bold", fontSize=13,
                    leading=16, textColor=BRAND_900)
STAT_NOTE = _style("stat-note", fontSize=6.5, leading=8.5, textColor=INK_400)

CELL = _style("cell", fontSize=7.5, leading=9.5, textColor=INK_700)
CELL_RIGHT = _style("cell-right", parent=CELL, alignment=TA_RIGHT, fontSize=7.5,
                    leading=9.5, textColor=INK_700)
CELL_STRONG = _style("cell-strong", fontName="Helvetica-Bold", fontSize=7.5,
                     leading=9.5, textColor=INK_900)
CELL_STRONG_RIGHT = _style("cell-strong-right", fontName="Helvetica-Bold",
                           fontSize=7.5, leading=9.5, textColor=INK_900,
                           alignment=TA_RIGHT)
CELL_MUTED = _style("cell-muted", fontSize=7.5, leading=9.5, textColor=INK_400)
HEAD = _style("head", fontName="Helvetica-Bold", fontSize=7, leading=9,
              textColor=colors.white)
HEAD_RIGHT = _style("head-right", fontName="Helvetica-Bold", fontSize=7,
                    leading=9, textColor=colors.white, alignment=TA_RIGHT)

#: The tone names a Stat may carry, mapped onto the figure's colour. Anything
#: unrecognised falls back to the brand, which is the right direction to fail in:
#: a figure in the wrong colour is a bug, a figure with no colour is a figure.
TONES = {
    "paid": STATE_COLOURS["paid"],
    "partial": STATE_COLOURS["partial"],
    "overdue": STATE_COLOURS["overdue"],
    "unpaid": STATE_COLOURS["unpaid"],
    "muted": INK_400,
    "brand": BRAND_900,
}


# ---------------------------------------------------------------------------
# The one drawn thing
# ---------------------------------------------------------------------------


class StackedBar(Flowable):
    """The report's charts, drawn.

    A row of filled rectangles on a rounded track -- the same picture the page
    draws in CSS, from the same percentages. Segments whose share rounds to
    nothing are skipped rather than drawn as a hairline, which would read as a
    category that exists at a size it does not have.

    Never the only statement of the figures: every caller prints the legend
    underneath, with each segment named and counted.
    """

    def __init__(self, segments, width: float, height: float = 7.0, gap: float = 1.2):
        super().__init__()
        self.segments = [s for s in segments if s.in_bar]
        self.width = width
        self.height = height
        self.gap = gap

    def wrap(self, available_width, available_height):
        self.width = min(self.width, available_width)
        return self.width, self.height

    def draw(self):
        canvas = self.canv
        radius = self.height / 2
        canvas.setFillColor(INK_200)
        canvas.roundRect(0, 0, self.width, self.height, radius, stroke=0, fill=1)

        # Clip to the rounded track so the first and last segments pick up its
        # corners instead of overhanging them with square ones.
        canvas.saveState()
        path = canvas.beginPath()
        path.roundRect(0, 0, self.width, self.height, radius)
        canvas.clipPath(path, stroke=0, fill=0)

        x = 0.0
        for segment in self.segments:
            share = _number(segment.share)
            span = self.width * share / 100.0
            if span <= 0.4:
                continue
            canvas.setFillColor(STATE_COLOURS.get(segment.key, BRAND_500))
            canvas.rect(x, 0, max(span - self.gap, 0.6), self.height,
                        stroke=0, fill=1)
            x += span
        canvas.restoreState()


def _number(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(report) -> bytes:
    """``report`` as a PDF, as bytes ready to hand to an HTTP response."""
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=PAGE_SIZE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        # Room for the running footer, which is drawn outside the frame.
        bottomMargin=MARGIN + 6 * mm,
        title=f"{report.title} — {report.scope_label}",
        author=PRODUCT_NAME,
        subject=report.period_label,
        creator=PRODUCT_NAME,
    )
    document.build(
        list(_flowables(report)),
        onFirstPage=lambda canvas, doc: _footer(canvas, doc, report),
        onLaterPages=lambda canvas, doc: _footer(canvas, doc, report),
    )
    return buffer.getvalue()


def _flowables(report):
    yield from _heading(report)
    yield Spacer(1, 6 * mm)

    if report.stats:
        yield _stats_grid(report.stats)
        yield Spacer(1, 6 * mm)

    for chart in report.charts:
        yield KeepTogether(list(_chart(chart)))
        yield Spacer(1, 5 * mm)

    for table in report.tables:
        yield from _table(table)

    if report.notes:
        yield Spacer(1, 3 * mm)
        yield Paragraph("How to read this report", SECTION)
        for note in report.notes:
            yield Paragraph(_escape(note.printed), NOTE)
            yield Spacer(1, 1.6 * mm)


def _heading(report):
    yield Paragraph(_escape(report.title), TITLE)
    yield Spacer(1, 1.5 * mm)
    yield Paragraph(_escape(report.scope_label), SUBTITLE)
    yield Spacer(1, 1 * mm)

    meta = [f"Period: {report.period_label}"]
    meta.append(f"Generated {report.generated_at.strftime('%-d %B %Y at %H:%M')}"
                if _supports_dash_d() else
                f"Generated {report.generated_at.strftime('%d %B %Y at %H:%M')}")
    if report.prepared_for:
        meta.append(f"Prepared for {report.prepared_for}")
    yield Paragraph(_escape("  ·  ".join(meta)), META)
    yield Spacer(1, 2.5 * mm)
    yield _rule(BRAND_900, 1.1)


def _supports_dash_d() -> bool:
    """``%-d`` strips a leading zero on glibc and raises on Windows.

    A report generated in development on Windows and in production on Linux
    should not differ by a leading zero, but it is a date on a cover page: worth
    the nicer form where it exists, not worth a dependency where it does not.
    """
    try:
        import datetime

        datetime.date(2026, 1, 5).strftime("%-d")
        return True
    except ValueError:
        return False


def _rule(colour, thickness: float) -> Table:
    """A horizontal rule. Platypus has no such flowable, so it is a table with
    one line and nothing in it."""
    rule = Table([[""]], colWidths=[CONTENT_WIDTH], rowHeights=[thickness])
    rule.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), thickness, colour),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return rule


def _stats_grid(stats) -> Table:
    """The headline figures, three across.

    Three rather than six in a row: these are the numbers somebody reads from
    across a desk, and six columns on A4 would make each of them 30mm wide.
    """
    per_row = 3
    cells = []
    for stat in stats:
        cells.append([
            Paragraph(_escape(stat.label.upper()), STAT_LABEL),
            Paragraph(_escape(stat.value.printed),
                      _style("v", parent=STAT_VALUE, fontName="Helvetica-Bold",
                             fontSize=13, leading=16,
                             textColor=TONES.get(stat.tone, BRAND_900))),
            Paragraph(_escape(stat.note), STAT_NOTE),
        ])

    rows = []
    for start in range(0, len(cells), per_row):
        chunk = cells[start:start + per_row]
        while len(chunk) < per_row:
            chunk.append([""])
        rows.append([_stat_box(box) for box in chunk])

    width = CONTENT_WIDTH / per_row
    grid = Table(rows, colWidths=[width] * per_row, hAlign="LEFT")
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return grid


def _stat_box(parts) -> Table:
    box = Table([[part] for part in parts], colWidths=[CONTENT_WIDTH / 3 - 8])
    box.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_50),
        ("LINEBEFORE", (0, 0), (0, -1), 1.6, BRAND_500),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return box


def _chart(chart):
    yield Paragraph(_escape(chart.title), SECTION)
    if chart.headline:
        yield Paragraph(
            f"<b>{_escape(chart.headline)}</b> {_escape(chart.caption)}",
            _style("headline", fontSize=10, leading=13, textColor=BRAND_900),
        )
        yield Spacer(1, 2 * mm)

    if chart.has_data:
        yield StackedBar(chart.segments, width=CONTENT_WIDTH)
        yield Spacer(1, 3 * mm)
    else:
        yield Paragraph(
            "Nothing to draw yet -- there is no priced total for this period to "
            "be a share of.",
            EMPTY,
        )
        yield Spacer(1, 2 * mm)

    # The legend, which is also the table view of the same figures.
    rows = [[
        _swatch(segment.key),
        Paragraph(_escape(segment.label), CELL),
        Paragraph(_escape(segment.value.printed), CELL_STRONG_RIGHT),
        Paragraph(_escape(segment.share_label), CELL_RIGHT),
    ] for segment in chart.segments]
    widths = [7, CONTENT_WIDTH * 0.45, CONTENT_WIDTH * 0.3, CONTENT_WIDTH * 0.2]
    legend = Table(rows, colWidths=widths, hAlign="LEFT")
    legend.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, INK_100),
    ]))
    yield legend


class _Swatch(Flowable):
    """The legend's colour chip. Five points square, and always beside a word."""

    def __init__(self, colour, size: float = 5.0):
        super().__init__()
        self.colour = colour
        self.width = self.height = size

    def draw(self):
        self.canv.setFillColor(self.colour)
        self.canv.circle(self.width / 2, self.height / 2, self.width / 2,
                         stroke=0, fill=1)


def _swatch(key: str) -> _Swatch:
    return _Swatch(STATE_COLOURS.get(key, BRAND_500))


def _table(table):
    """One titled grid, with its heading kept with at least its first rows."""
    printable = table.printable_columns
    columns = [table.columns[i] for i in printable]

    heading = [Paragraph(_escape(table.title), SECTION)]
    if not table.rows:
        yield KeepTogether(heading + [Paragraph(_escape(table.empty), EMPTY)])
        yield Spacer(1, 5 * mm)
        return

    header = [
        Paragraph(_escape(column.label),
                  HEAD_RIGHT if column.align == RIGHT else HEAD)
        for column in columns
    ]
    body = [
        [_paragraph(row[i], columns[n]) for n, i in enumerate(printable)]
        for row in table.rows
    ]
    footer = (
        [[_paragraph(table.total[i], columns[n], strong=True)
          for n, i in enumerate(printable)]]
        if table.total
        else []
    )

    grid = Table(
        [header] + body + footer,
        colWidths=_widths(columns),
        repeatRows=1,
        hAlign="LEFT",
    )
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_900),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, INK_200),
        # Every other body row tinted, which is what makes a nine-column row of
        # figures readable across on paper without ruling every column.
        *[
            ("BACKGROUND", (0, n), (-1, n), INK_100)
            for n in range(2, len(body) + 1, 2)
        ],
    ]
    if footer:
        style += [
            ("BACKGROUND", (0, -1), (-1, -1), BRAND_50),
            ("LINEABOVE", (0, -1), (-1, -1), 0.8, BRAND_900),
            ("LINEBELOW", (0, -1), (-1, -1), 0, colors.white),
        ]
    grid.setStyle(TableStyle(style))

    tail = [*heading, grid]
    if table.note is not None:
        tail += [Spacer(1, 1.5 * mm), Paragraph(_escape(table.note.printed), NOTE)]

    # A short table travels with its own title, so a section never begins with
    # its heading alone at the foot of a page. A long one is allowed to start
    # where it lands: holding forty rows of outstanding balances together would
    # push the lot onto a fresh page and leave most of one blank.
    if len(body) <= 12:
        yield KeepTogether(tail)
    else:
        yield from tail
    yield Spacer(1, 6 * mm)


def _widths(columns) -> list[float]:
    total = sum(column.weight for column in columns) or 1
    return [CONTENT_WIDTH * column.weight / total for column in columns]


def _paragraph(cell, column, *, strong: bool = False) -> Paragraph:
    right = column.align == RIGHT
    if strong or cell.strong:
        style = CELL_STRONG_RIGHT if right else CELL_STRONG
    elif cell.muted:
        style = _style("muted-cell", parent=CELL_MUTED, fontSize=7.5, leading=9.5,
                       textColor=INK_400,
                       alignment=TA_RIGHT if right else TA_LEFT)
    else:
        style = CELL_RIGHT if right else CELL
    return Paragraph(_escape(cell.printed), style)


def _footer(canvas, document, report) -> None:
    """The running foot: what this is, and which page of it you are holding."""
    canvas.saveState()
    y = MARGIN + 2 * mm
    canvas.setStrokeColor(INK_200)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, y + 8, PAGE_SIZE[0] - MARGIN, y + 8)

    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(INK_400)
    canvas.drawString(
        MARGIN, y,
        f"{PRODUCT_NAME} · {report.title} · {report.scope_label}",
    )
    canvas.drawRightString(PAGE_SIZE[0] - MARGIN, y, f"Page {document.page}")
    canvas.restoreState()


def _escape(text) -> str:
    """Paragraph text is mini-HTML, so a school called "Smith & Sons" has to be
    escaped or ReportLab reads it as a broken entity and drops the rest of the
    line."""
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
