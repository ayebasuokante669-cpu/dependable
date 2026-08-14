"""What each role is allowed to *do*, as distinct from what it can *see*.

Tenant scoping (``apps.core.tenancy``) answers "which rows?".  Capabilities
answer "which actions?".  Keeping them apart means a bursar can be given full
visibility of the academic setup while still being unable to change it.

Capabilities are granted by permission role only -- never by job title.
"""

from __future__ import annotations

from enum import Enum

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from .roles import Role


class Capability(str, Enum):
    """A single thing an account may be permitted to do."""

    VIEW_ACADEMICS = "view_academics"
    MANAGE_ACADEMICS = "manage_academics"


_VIEW_ONLY = frozenset({Capability.VIEW_ACADEMICS})
_ACADEMIC_ADMIN = frozenset({Capability.VIEW_ACADEMICS, Capability.MANAGE_ACADEMICS})

#: The grant table. Add a capability here, not with an ad-hoc check in a view.
ROLE_CAPABILITIES: dict[str, frozenset[Capability]] = {
    Role.PLATFORM_OWNER: _ACADEMIC_ADMIN,
    Role.SCHOOL_OWNER: _ACADEMIC_ADMIN,
    Role.PRINCIPAL: _ACADEMIC_ADMIN,
    # A bursar runs the money, not the curriculum: read-only on academic setup.
    Role.BURSAR: _VIEW_ONLY,
}


def capabilities_for(role: str | None) -> frozenset[Capability]:
    """Capabilities granted to ``role``. Unknown roles get nothing."""
    if not role:
        return frozenset()
    return ROLE_CAPABILITIES.get(role, frozenset())


def capabilities_of(user) -> frozenset[Capability]:
    if user is None or not getattr(user, "is_authenticated", False):
        return frozenset()
    if getattr(user, "is_superuser", False):
        return _ACADEMIC_ADMIN
    return capabilities_for(getattr(user, "role", None))


def has_capability(user, capability: Capability) -> bool:
    return capability in capabilities_of(user)


class CapabilityRequiredMixin(LoginRequiredMixin):
    """Refuse the view unless the account holds :attr:`capability`.

    Signed-out visitors fall through to ``LoginRequiredMixin`` and are
    redirected to the login page; signed-in accounts without the capability get
    a 403, because bouncing them to a login form they have already passed would
    be a lie about what went wrong.
    """

    capability: Capability | None = None

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.capability is not None:
            if not has_capability(request.user, self.capability):
                raise PermissionDenied(
                    f"Your role does not allow {self.capability.value.replace('_', ' ')}."
                )
        return super().dispatch(request, *args, **kwargs)
