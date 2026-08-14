"""Money formatting shared by fees today and payments later."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()

NAIRA = "₦"


@register.filter
def naira(value) -> str:
    """Format an amount as Naira: ``₦102,000`` or ``₦1,250.50``.

    Kobo are shown only when there are any, because school fees are almost
    always whole Naira and a screen full of ``.00`` is just noise.
    """
    if value is None or value == "":
        return "—"
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return "—"
    if amount == amount.to_integral_value():
        return f"{NAIRA}{amount:,.0f}"
    return f"{NAIRA}{amount:,.2f}"
