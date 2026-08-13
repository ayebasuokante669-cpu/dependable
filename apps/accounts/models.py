"""The custom user model.

Swapped in before the first migration, because ``AUTH_USER_MODEL`` cannot be
changed afterwards without a painful data migration.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models

from apps.core.roles import Role, Scope, scope_for
from apps.core.tenancy import TenantQuerySet, get_current_context


class UserQuerySet(TenantQuerySet):
    tenant_school_field = "school_id"
    tenant_branch_field = "branch_id"
    # A user with no branch belongs to the school as a whole (e.g. the owner),
    # and branch staff should be able to see them in directories.
    tenant_include_school_wide = True


class DependableUserManager(UserManager.from_queryset(UserQuerySet)):
    """Unscoped user manager -- named, because migrations must serialise it.

    Keeps ``create_user``/``create_superuser`` and adds the tenancy queryset
    helpers without applying any filtering of its own.
    """

    use_in_migrations = True


class ScopedUserManager(DependableUserManager):
    """Tenant-filtered view of the user table, exposed as ``User.scoped``."""

    # Migrations must never run through tenant filtering.
    use_in_migrations = False

    def get_queryset(self) -> UserQuerySet:
        return super().get_queryset().for_context(get_current_context())


class User(AbstractUser):
    """A person with an account.

    ``role`` is the permission role -- what they may see.  ``job_title`` is what
    they do at the school.  The two are intentionally separate fields: promoting
    a bursar to "Head of Finance" is an HR change, not an access change.
    """

    school = models.ForeignKey(
        "schools.School",
        on_delete=models.CASCADE,
        related_name="users",
        null=True,
        blank=True,
        help_text="The tenant this account belongs to. Blank for platform staff.",
    )
    branch = models.ForeignKey(
        "schools.Branch",
        on_delete=models.SET_NULL,
        related_name="users",
        null=True,
        blank=True,
        help_text="Optional. Leave blank for school-wide accounts.",
    )
    role = models.CharField(
        "permission role",
        max_length=32,
        choices=Role.choices,
        default=Role.BURSAR,
        help_text="Access level. Determines which rows and screens are visible.",
    )
    job_title = models.CharField(
        max_length=120,
        blank=True,
        help_text="Their job at the school, e.g. 'Head of Mathematics'. "
        "Has no effect on permissions.",
    )
    phone = models.CharField(max_length=32, blank=True)

    # ``objects`` must stay unscoped: authentication backends and
    # ``createsuperuser`` resolve users through the default manager, before any
    # tenant context exists.  Use ``User.scoped`` for tenant-facing listings.
    objects = DependableUserManager()
    scoped = ScopedUserManager()

    class Meta:
        ordering = ["last_name", "first_name", "username"]
        indexes = [models.Index(fields=["school", "branch"])]

    def __str__(self) -> str:
        return self.get_full_name() or self.username

    @property
    def scope(self) -> Scope:
        return scope_for(self.role)

    @property
    def is_platform_staff(self) -> bool:
        return self.scope is Scope.PLATFORM or self.is_superuser

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError

        if self.branch_id and self.school_id:
            if self.branch.school_id != self.school_id:
                raise ValidationError(
                    {"branch": "Branch must belong to the selected school."}
                )
        if self.role != Role.PLATFORM_OWNER and not self.school_id:
            raise ValidationError(
                {"school": "Non-platform accounts must belong to a school."}
            )
