"""The tenant itself: a School, and the Branches (campuses) beneath it."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

from apps.core.models import TimeStampedModel
from apps.core.tenancy import TenantManager, TenantQuerySet, UnscopedManager

#: What a school may upload as its logo, and how big. Deliberately the same
#: shape of rule as the payment-receipt upload: a school's logo arrives as a
#: PNG someone exported from a design, or a JPEG photographed off a signboard.
LOGO_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
LOGO_MAX_BYTES = 2 * 1024 * 1024


def validate_logo(upload) -> None:
    """Reject anything that is not a reasonably sized image.

    A ``FileField`` with this validator rather than an ``ImageField``, which
    would pull in Pillow for the sake of reading a header. The receipt upload
    on ``payments.Payment`` made the same trade, and a dependency the school's
    server then has to keep patched is not free.
    """
    name = (getattr(upload, "name", "") or "").lower()
    if name and not any(name.endswith(ext) for ext in sorted(LOGO_TYPES)):
        raise ValidationError(
            "Upload the logo as a PNG, JPEG, WebP or SVG image."
        )
    size = getattr(upload, "size", 0) or 0
    if size > LOGO_MAX_BYTES:
        raise ValidationError(
            f"That file is {size // 1024 // 1024} MB. Keep the logo under "
            f"{LOGO_MAX_BYTES // 1024 // 1024} MB so it loads on a slow "
            f"connection."
        )


def logo_upload_to(instance: "School", filename: str) -> str:
    """One folder per school, keyed by slug so the path reads in a file browser."""
    return f"logos/{instance.slug or instance.pk or 'school'}/{filename}"


class Plan(models.TextChoices):
    TRIAL = "trial", "Trial"
    STARTER = "starter", "Starter"
    GROWTH = "growth", "Growth"
    ENTERPRISE = "enterprise", "Enterprise"


class SchoolStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    TRIALING = "trialing", "Trialing"
    PAST_DUE = "past_due", "Past due"
    SUSPENDED = "suspended", "Suspended"
    CANCELLED = "cancelled", "Cancelled"


class SchoolQuerySet(TenantQuerySet):
    # A school *is* the tenant, so the school column on this table is its own pk.
    tenant_school_field = "id"
    tenant_branch_field = None


class SchoolManager(TenantManager.from_queryset(SchoolQuerySet)):
    """Auto-scoped: a school owner querying schools sees only their own."""


class SchoolUnscopedManager(UnscopedManager.from_queryset(SchoolQuerySet)):
    """Every school, regardless of caller. Used by admin FK dropdowns and jobs."""


class School(TimeStampedModel):
    """A tenant.  Every other row on the platform ultimately belongs to one."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.TRIAL)
    status = models.CharField(
        max_length=20, choices=SchoolStatus.choices, default=SchoolStatus.TRIALING
    )
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    logo = models.FileField(
        upload_to=logo_upload_to,
        validators=[validate_logo],
        null=True,
        blank=True,
        help_text="Optional. Appears on your screens and on payment receipts. "
        "Without one, your school's initial is shown instead.",
    )

    objects = SchoolManager()
    all_objects = SchoolUnscopedManager()

    class Meta:
        ordering = ["name"]
        base_manager_name = "all_objects"
        default_manager_name = "objects"

    def __str__(self) -> str:
        return self.name

    @property
    def is_active(self) -> bool:
        return self.status in {SchoolStatus.ACTIVE, SchoolStatus.TRIALING}

    @property
    def initial(self) -> str:
        """The letter shown when there is no logo.

        Falls back to a question mark rather than an empty square, so a
        half-created school still renders as something on purpose.
        """
        return (self.name.strip()[:1] or "?").upper()

    @property
    def has_logo(self) -> bool:
        return bool(self.logo)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        base = slugify(self.name) or "school"
        slug, counter = base, 2
        while School.all_objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base}-{counter}"
            counter += 1
        return slug


class BranchQuerySet(TenantQuerySet):
    tenant_school_field = "school_id"
    # For the branch table itself the "branch column" is the primary key, and a
    # branch is never school-wide, so shared rows do not apply.
    tenant_branch_field = "id"
    tenant_include_school_wide = False


class BranchManager(TenantManager.from_queryset(BranchQuerySet)):
    """Auto-scoped: branch staff see their own campus, school owners see all."""


class BranchUnscopedManager(UnscopedManager.from_queryset(BranchQuerySet)):
    """Every branch, regardless of caller."""


class Branch(TimeStampedModel):
    """A campus of a school.  The second, narrower axis of tenant isolation."""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="branches")
    name = models.CharField(max_length=200)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    address = models.TextField(blank=True)
    head = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="branches_headed",
        null=True,
        blank=True,
        help_text="Principal or branch administrator responsible for this campus.",
    )
    is_active = models.BooleanField(default=True)

    objects = BranchManager()
    all_objects = BranchUnscopedManager()

    class Meta:
        ordering = ["school__name", "name"]
        verbose_name_plural = "branches"
        base_manager_name = "all_objects"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(
                fields=["school", "name"], name="unique_branch_name_per_school"
            )
        ]

    def __str__(self) -> str:
        return f"{self.school.name} - {self.name}"
