"""Turning a figure into a bar width.

The charts on the dashboards are CSS and inline SVG -- no charting library, no
runtime, nothing to load -- so the one thing a template genuinely cannot do for
itself is arithmetic. That is all this is.

Deliberately *only* proportions. Nothing here decides what a figure means, and
no template is allowed to total a column with it: every amount on a dashboard
is produced by the view from the same derivation the rest of the platform uses
(see apps/payments/balances.py). These filters take a number that has already
been worked out and say how wide to draw it.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _number(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None


@register.filter
def percent_of(value, total) -> str:
    """``value`` as a percentage of ``total``, for a CSS width.

    Returns a bare number with two decimals -- "37.50" -- because it is
    interpolated into ``--bar: {{ x|percent_of:y }}%`` and a locale-formatted
    string would not be valid CSS.

    A zero or missing total is "0", never a division error: a term with nothing
    priced yet is a real state that a dashboard has to draw, and it draws as an
    empty track.
    """
    amount, whole = _number(value), _number(total)
    if amount is None or whole is None or whole <= 0:
        return "0"
    share = (amount / whole) * 100
    # Clamped: a class that has over-collected is still a full bar, not one
    # that runs past the end of its own track.
    share = min(max(share, Decimal("0")), Decimal("100"))
    return f"{share:.2f}"


@register.filter
def percent_label(value, total) -> str:
    """The same proportion as a whole number for a human -- "38%".

    Separate from :func:`percent_of` on purpose. The width wants precision so
    that bars of similar size stay distinguishable; the label wants to be read
    at a glance, and "37.50%" on a dashboard is false precision about a figure
    that changes every time a parent pays.
    """
    amount, whole = _number(value), _number(total)
    if amount is None or whole is None or whole <= 0:
        return "—"
    return f"{(amount / whole) * 100:.0f}%"
