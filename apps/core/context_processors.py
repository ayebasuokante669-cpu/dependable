"""Template context shared by every page in the shell."""

from __future__ import annotations

from .navigation import nav_for
from .permissions import capabilities_of
from .roles import Role, scope_for


def tenancy(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav_sections": [], "tenant": None, "capabilities": frozenset()}

    context = getattr(request, "tenant", None)
    # The effective role, which promotes Django superusers to platform scope.
    role = context.role if context is not None else getattr(user, "role", None)
    return {
        "nav_sections": nav_for(role, request.path),
        "tenant": context,
        "current_school": getattr(user, "school", None),
        "current_branch": getattr(user, "branch", None),
        "current_role_label": Role(role).label if role in Role.values else "",
        "current_scope": scope_for(role).value,
        # Plain strings so templates can write {% if "manage_academics" in capabilities %}
        "capabilities": {c.value for c in capabilities_of(user)},
    }
