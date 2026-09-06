"""Upload rules for the papers an applicant sends in.

Same shape of rule, and the same trade, as the school logo and the payment
receipt: a ``FileField`` with an explicit validator rather than an
``ImageField``, so nothing here drags Pillow -- and a dependency the school's
server then has to keep patched -- into the project for the sake of reading an
image header.

What arrives is a photo taken on a phone or a PDF a cyber cafe produced, so
both are accepted and the size ceiling is set for a slow connection rather than
for a scanner.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

#: A passport photograph is an image. Everything else may also be a scan.
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".heic"}
DOCUMENT_TYPES = IMAGE_TYPES | {".pdf"}

#: 5 MB. A phone photo is 2-4 MB, and a parent on mobile data has to be able to
#: send one without the upload timing out.
DOCUMENT_MAX_BYTES = 5 * 1024 * 1024


def _validate(upload, allowed: set[str], noun: str) -> None:
    name = (getattr(upload, "name", "") or "").lower()
    if name and not any(name.endswith(ext) for ext in allowed):
        readable = ", ".join(sorted(e.lstrip(".").upper() for e in allowed))
        raise ValidationError(f"Upload the {noun} as {readable}.")
    size = getattr(upload, "size", 0) or 0
    if size > DOCUMENT_MAX_BYTES:
        megabytes = size / 1024 / 1024
        raise ValidationError(
            f"That file is {megabytes:.1f} MB. Keep it under "
            f"{DOCUMENT_MAX_BYTES // 1024 // 1024} MB so it uploads on a phone."
        )


def validate_photo(upload) -> None:
    """A passport photograph: an image, never a PDF."""
    _validate(upload, IMAGE_TYPES, "photograph")


def validate_document(upload) -> None:
    """A supporting document: a scan or a photo of one."""
    _validate(upload, DOCUMENT_TYPES, "document")
