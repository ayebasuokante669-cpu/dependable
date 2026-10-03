"""Building the workbooks the import tests upload.

Not a test module -- a helper the four import test suites share, so that "what a
school's file looks like" is described in one place. Deliberately *not* using
:func:`apps.core.spreadsheets.build_template`: a test that built its fixture with
the code under test would pass even if the template and the reader drifted apart.
These sheets are assembled the way openpyxl assembles any sheet, which is how a
school's own export arrives.
"""

from __future__ import annotations

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook

from .imports import Sheet

XLSX_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def build_workbook(
    sheet: Sheet,
    rows,
    *,
    headers: list[str] | None = None,
    guidance_row: bool = False,
    title_row: str = "",
    sheet_name: str | None = None,
) -> bytes:
    """A workbook shaped like something a school would actually upload.

    ``rows`` are dicts keyed by column key. ``headers`` overrides the order and
    the spelling of the header row, which is how the reordered-columns and
    header-alias cases are exercised. ``title_row`` puts a line and a blank row
    above the headers, the way a school's own export does.
    """
    keys = headers or [column.key for column in sheet.columns]
    labels = [
        sheet.by_key[key].label if key in sheet.by_key else key for key in keys
    ]

    book = Workbook()
    page = book.active
    page.title = sheet_name or sheet.name
    if title_row:
        page.append([title_row])
        page.append([])
    page.append(labels)
    if guidance_row:
        # The real thing, marker and all -- a file that left the template's
        # example row in place is the case being exercised.
        page.append([
            sheet.guidance_for(sheet.by_key[key]) if key in sheet.by_key else ""
            for key in keys
        ])
    for row in rows:
        page.append([row.get(key, "") for key in keys])

    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def upload(content: bytes, name: str = "import.xlsx") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type=XLSX_TYPE)
