"""The product's own name and mark, in one place.

The platform has been called three things during development -- an internal
codename, a working title, and now SCHOOLCORD -- and the last rename left the
old names scattered across page titles, the sidebar, password-reset email and
the admin header. One constant, read by settings, the admin and every template
through a context processor, is what stops that happening a fourth time.

This is the *product's* identity. A tenant school's own name and logo are on
:class:`apps.schools.models.School`, and the two are never interchangeable: a
parent reading a receipt wants their school's name on it, and a proprietor
signing in wants to know whose software they are signing in to.
"""

from __future__ import annotations

#: What every user-facing surface calls this software.
PRODUCT_NAME = "SCHOOLCORD"

#: One line, used on the landing page and in the footer.
PRODUCT_TAGLINE = "School management that starts with the fees"

#: The letter in the brand mark when the full logo will not fit.
PRODUCT_INITIAL = "S"

#: Sent as the From: name on transactional mail.
SUPPORT_EMAIL = "no-reply@schoolcord.app"


def branding(request=None) -> dict:
    """Context processor: the product name on every page, signed in or out.

    Deliberately separate from ``core.context_processors.tenancy``, which
    returns early for anonymous visitors -- the landing page and the login form
    are exactly where the product's name matters most.
    """
    return {
        "product_name": PRODUCT_NAME,
        "product_tagline": PRODUCT_TAGLINE,
        "product_initial": PRODUCT_INITIAL,
    }
