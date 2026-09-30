"""Asking "what is left to set up?" from a template that was not built for it.

The four onboarding destinations -- the class list, the fee structures, the
roster, the staff list -- belong to four different apps, and none of their views
knows anything about onboarding. Before this, finishing a step landed you back on
one of those lists with nothing to say where to go next, which is the dead end
this exists to close.

A tag rather than a context processor, deliberately. ``setup_progress`` is half a
dozen counts, and a context processor would run them on every page in the app to
serve four -- including every page of a school that finished setting up months
ago. Here the cost lands only on the screens that ask.
"""

from __future__ import annotations

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def onboarding_progress(context):
    """``{% onboarding_progress as setup %}`` -- the checklist and what is next.

    Returns ``None`` for anyone it cannot answer for: a signed-out visitor, or an
    account with no school. The template then renders nothing, which is the right
    answer for both -- neither of them has a school to set up.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "school_id", None) is None:
        # Platform staff. They have no school of their own, so there is no
        # checklist -- and the prompts this feeds would be about somebody else's.
        return None

    from apps.core.views import setup_progress

    return setup_progress(request)
