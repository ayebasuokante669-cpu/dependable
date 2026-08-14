"""The row-level multi-tenancy core.

Every request installs a :class:`TenantContext` derived from ``request.user``.
Tenant-scoped managers read that context and narrow their queryset before any
view code runs, so isolation is a property of the model layer rather than
something each view has to remember.

Outside the request/response cycle (shell, migrations, management commands) no
context is installed and querysets are *not* filtered -- otherwise
``migrate`` and ``createsuperuser`` could not see their own rows.  Anything
running as a real tenant in the background should wrap its work in
:func:`scope_to`.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from django.db import models
from django.db.models import Q

from .roles import Role, Scope, scope_for


@dataclass(frozen=True)
class TenantContext:
    """Who is asking, and therefore which rows they may see."""

    school_id: int | None = None
    branch_id: int | None = None
    role: str | None = None
    is_authenticated: bool = False
    #: When true, scoping is suspended entirely (see :func:`unscoped`).
    bypass: bool = False

    @property
    def scope(self) -> Scope:
        return Scope.PLATFORM if self.bypass else scope_for(self.role)

    @classmethod
    def from_user(cls, user) -> "TenantContext":
        if user is None or not getattr(user, "is_authenticated", False):
            return cls(is_authenticated=False)
        role = getattr(user, "role", None)
        if getattr(user, "is_superuser", False):
            # A Django superuser is platform staff by definition. Without this,
            # an account from `createsuperuser` -- which gets the default role
            # and no school -- would be scoped down to zero rows and find an
            # empty admin.
            role = Role.PLATFORM_OWNER
        return cls(
            school_id=getattr(user, "school_id", None),
            branch_id=getattr(user, "branch_id", None),
            role=role,
            is_authenticated=True,
        )


_current: ContextVar[TenantContext | None] = ContextVar(
    "dependable_tenant_context", default=None
)


def get_current_context() -> TenantContext | None:
    """The context for the request being served, or ``None`` outside one."""
    return _current.get()


def activate(context: TenantContext):
    """Install ``context``.  Returns a token to hand back to :func:`deactivate`."""
    return _current.set(context)


def deactivate(token) -> None:
    _current.reset(token)


@contextmanager
def scope_to(
    *, school_id: int | None = None, branch_id: int | None = None, role: str | None = None
) -> Iterator[TenantContext]:
    """Run a block as if a user of ``role`` at that school/branch were asking.

    Use this for background jobs, imports and scheduled tasks -- anywhere there
    is no request but tenant isolation should still hold.
    """
    context = TenantContext(
        school_id=school_id, branch_id=branch_id, role=role, is_authenticated=True
    )
    token = activate(context)
    try:
        yield context
    finally:
        deactivate(token)


@contextmanager
def unscoped() -> Iterator[None]:
    """Suspend tenant filtering for the duration of the block.

    The deliberate escape hatch for cross-tenant reporting and support tooling.
    Every use should be obvious in review, which is why it reads as a `with`
    block rather than a keyword argument buried in a queryset call.
    """
    current = get_current_context() or TenantContext()
    token = activate(
        TenantContext(
            school_id=current.school_id,
            branch_id=current.branch_id,
            role=current.role,
            is_authenticated=current.is_authenticated,
            bypass=True,
        )
    )
    try:
        yield
    finally:
        deactivate(token)


def apply_scope(
    queryset: models.QuerySet,
    context: TenantContext | None = None,
    *,
    school_field: str = "school_id",
    branch_field: str | None = "branch_id",
    include_school_wide: bool = True,
) -> models.QuerySet:
    """Narrow ``queryset`` to the rows ``context`` is allowed to see.

    ``branch_field`` may be ``None`` for models that only carry a school.
    ``include_school_wide`` keeps rows with a null branch visible to
    branch-scoped users -- a school-wide fee structure should be readable by
    every bursar, not just the one at the branch that happens to own it.
    """
    if context is None or context.bypass:
        # No request in flight (shell, migrations, tests) -- do not filter.
        return queryset
    if not context.is_authenticated:
        return queryset.none()

    scope = context.scope
    if scope is Scope.PLATFORM:
        return queryset
    if context.school_id is None:
        # A non-platform account with no school cannot own any row.
        return queryset.none()

    queryset = queryset.filter(**{school_field: context.school_id})
    if scope is Scope.SCHOOL or branch_field is None:
        return queryset

    # Branch scope from here down.
    if context.branch_id is None:
        # Branch-scoped role with no branch assigned: fail closed.
        return queryset.none()
    condition = Q(**{branch_field: context.branch_id})
    if include_school_wide:
        condition |= Q(**{f"{branch_field.removesuffix('_id')}__isnull": True})
    return queryset.filter(condition)


class TenantQuerySet(models.QuerySet):
    """Queryset that knows how to narrow itself to a tenant."""

    #: Overridden by subclasses whose columns are named differently.
    tenant_school_field = "school_id"
    tenant_branch_field: str | None = "branch_id"
    tenant_include_school_wide = True

    def for_context(self, context: TenantContext | None = None) -> "TenantQuerySet":
        return apply_scope(
            self,
            context,
            school_field=self.tenant_school_field,
            branch_field=self.tenant_branch_field,
            include_school_wide=self.tenant_include_school_wide,
        )

    def for_current_tenant(self) -> "TenantQuerySet":
        return self.for_context(get_current_context())


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Default manager that filters by the active tenant automatically.

    This is what makes isolation the default: ``Student.objects.all()`` in a
    view is already narrowed to the caller's school and branch.
    """

    def get_queryset(self) -> TenantQuerySet:
        return super().get_queryset().for_current_tenant()


class UnscopedManager(models.Manager.from_queryset(TenantQuerySet)):
    """Manager that never filters.  Exposed as ``Model.all_objects``."""
