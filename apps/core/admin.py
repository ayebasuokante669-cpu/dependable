"""Admin plumbing that keeps the Django admin inside tenant boundaries."""

from __future__ import annotations

from django.contrib import admin

from .roles import Scope, scope_for
from .tenancy import TenantContext, apply_scope


class TenantScopedAdminMixin(admin.ModelAdmin):
    """Restrict an admin's rows -- and its FK dropdowns -- to the caller's tenant.

    Models using :class:`~apps.core.tenancy.TenantManager` are already filtered
    by the manager itself.  This mixin covers the tenancy tables (``School``,
    ``Branch``, ``User``) whose default managers stay unscoped because auth and
    ``createsuperuser`` depend on them, and it narrows the related-field
    dropdowns so a school owner cannot reassign a row to a school they do not
    own.
    """

    #: Lookup path from this model to the school, e.g. ``"school_id"`` or ``"id"``.
    tenant_school_field = "school_id"
    #: Lookup path to the branch, or ``None`` if the model has no branch.
    tenant_branch_field: str | None = "branch_id"

    def _context(self, request) -> TenantContext | None:
        context = getattr(request, "tenant", None)
        if context is None:
            return None
        # Django superusers are platform staff regardless of the role field.
        if request.user.is_superuser:
            return None
        return context

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        context = self._context(request)
        if context is None:
            return queryset
        return apply_scope(
            queryset,
            context,
            school_field=self.tenant_school_field,
            branch_field=self.tenant_branch_field,
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        context = self._context(request)
        if context is not None and context.school_id is not None:
            if db_field.name == "school":
                kwargs.setdefault(
                    "queryset", db_field.related_model.all_objects.filter(
                        pk=context.school_id
                    )
                )
            elif db_field.name == "branch":
                queryset = db_field.related_model.all_objects.filter(
                    school_id=context.school_id
                )
                if scope_for(context.role) is Scope.BRANCH and context.branch_id:
                    queryset = queryset.filter(pk=context.branch_id)
                kwargs.setdefault("queryset", queryset)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
