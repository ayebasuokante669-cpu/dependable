"""Template context shared by every page in the shell."""

from __future__ import annotations

from django.apps import apps as django_apps

from .navigation import nav_for
from .permissions import capabilities_of
from .roles import Role, scope_for


def _capability_strings(user) -> set[str]:
    """Every capability this account holds, as plain strings.

    Mostly the static role table. Admissions adds the one grant that is not a
    property of the role at all -- whether a bursar may see the applicant
    pipeline is the *school's* decision -- so it is asked for its answer rather
    than having its rule restated here. Resolved through the app registry, the
    same guard the bursar dashboard uses for payments: the shell has to render
    whether or not the add-on is installed.
    """
    granted = {c.value for c in capabilities_of(user)}
    if django_apps.is_installed("apps.admissions"):
        from apps.admissions.access import admissions_capabilities

        granted |= admissions_capabilities(user)
    return granted


def tenancy(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav_sections": [], "tenant": None, "capabilities": frozenset()}

    context = getattr(request, "tenant", None)
    # The effective role, which promotes Django superusers to platform scope.
    role = context.role if context is not None else getattr(user, "role", None)
    capabilities = _capability_strings(user)
    return {
        # Capability-gated nav entries need the same answer the views will
        # give, or the sidebar offers a link that 403s.
        "nav_sections": nav_for(role, request.path, capabilities),
        "tenant": context,
        "current_school": getattr(user, "school", None),
        "current_branch": getattr(user, "branch", None),
        "current_role_label": Role(role).label if role in Role.values else "",
        "current_scope": scope_for(role).value,
        # Plain strings so templates can write {% if "manage_academics" in capabilities %}
        "capabilities": capabilities,
    }
