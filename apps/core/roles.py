"""Permission roles and the access scope each one implies.

A role answers "how much of the platform may this account see?".  It is
deliberately *not* the same thing as the job the person does at the school --
that lives on ``User.job_title``.  A user whose job title is "Head of
Mathematics" may hold the ``principal`` role; a "Finance Officer" may hold
``bursar``.  Keeping the two apart means job titles can be free-form and
school-specific without ever widening someone's access.
"""

from __future__ import annotations

from enum import Enum

from django.db import models


class Scope(str, Enum):
    """How wide a role's row-level visibility is."""

    PLATFORM = "platform"  # every school on the platform
    SCHOOL = "school"      # every branch of one school
    BRANCH = "branch"      # a single branch of one school


class Role(models.TextChoices):
    """Permission role -- the access level attached to a user account."""

    PLATFORM_OWNER = "platform_owner", "Platform Owner"
    SCHOOL_OWNER = "school_owner", "School Owner"
    PRINCIPAL = "principal", "Principal / Branch Admin"
    BURSAR = "bursar", "Bursar"


#: The single source of truth mapping a role to its data visibility.
ROLE_SCOPES: dict[str, Scope] = {
    Role.PLATFORM_OWNER: Scope.PLATFORM,
    Role.SCHOOL_OWNER: Scope.SCHOOL,
    Role.PRINCIPAL: Scope.BRANCH,
    Role.BURSAR: Scope.BRANCH,
}


def scope_for(role: str | None) -> Scope:
    """Return the :class:`Scope` for ``role``.

    Unknown or missing roles fall back to the narrowest scope so that a
    misconfigured account fails closed rather than leaking another tenant's rows.
    """
    if not role:
        return Scope.BRANCH
    return ROLE_SCOPES.get(role, Scope.BRANCH)


def is_platform_role(role: str | None) -> bool:
    return scope_for(role) is Scope.PLATFORM
