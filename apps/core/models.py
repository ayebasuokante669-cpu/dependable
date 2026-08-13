"""Abstract building blocks every tenant-owned model inherits."""

from __future__ import annotations

from django.db import models

from .tenancy import TenantManager, UnscopedManager, get_current_context


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantScopedModel(TimeStampedModel):
    """Base class for anything owned by a school.

    Carries ``school_id`` and ``branch_id``, fills them in from the active
    request when they are left blank, and ships a manager that filters every
    query by the caller's tenant.  Feature models (students, invoices,
    messages) should subclass this and never think about isolation again.

    ``branch`` is nullable on purpose: some records belong to the school as a
    whole rather than to one campus.
    """

    school = models.ForeignKey(
        "schools.School",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
    )
    branch = models.ForeignKey(
        "schools.Branch",
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_set",
        null=True,
        blank=True,
    )

    objects = TenantManager()
    all_objects = UnscopedManager()

    class Meta:
        abstract = True
        # Related-object traversal and admin internals use the base manager;
        # keeping it unfiltered avoids a scoped query silently breaking a
        # `obj.some_fk` access during an unscoped block.
        base_manager_name = "all_objects"
        default_manager_name = "objects"
        indexes = [
            models.Index(fields=["school", "branch"]),
        ]

    def save(self, *args, **kwargs):
        context = get_current_context()
        if context is not None and context.is_authenticated:
            if self.school_id is None:
                self.school_id = context.school_id
            if self.branch_id is None:
                self.branch_id = context.branch_id
        super().save(*args, **kwargs)
