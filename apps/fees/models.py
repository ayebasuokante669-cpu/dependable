"""Fee structures: what each class owes per term.

The academic-finance data that payments will later be recorded against. Three
models, all branch-owned so they inherit tenant scoping:

    Term  --<  FeeStructure  --<  FeeComponent

A structure's total is always the sum of its components. It is never stored --
a stored total is a second source of truth that drifts the first time someone
edits a line item, and reconciling a payment against a stale figure is the kind
of bug that costs a school real money.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction

from apps.core.models import BranchScopedModel


class TermSequence(models.IntegerChoices):
    FIRST = 1, "First Term"
    SECOND = 2, "Second Term"
    THIRD = 3, "Third Term"


class Term(BranchScopedModel):
    """One term of an academic year at a branch, e.g. "First Term 2025/2026"."""

    branch = models.ForeignKey(
        "schools.Branch", on_delete=models.CASCADE, related_name="terms"
    )
    name = models.CharField(max_length=120, help_text='e.g. "First Term 2025/2026".')
    academic_year = models.CharField(
        max_length=20, help_text='e.g. "2025/2026".'
    )
    sequence = models.PositiveSmallIntegerField(
        choices=TermSequence.choices, default=TermSequence.FIRST
    )
    is_current = models.BooleanField(
        default=False,
        help_text="The term the school is in now. Only one per branch; setting "
        "this clears it from any other term.",
    )

    class Meta(BranchScopedModel.Meta):
        abstract = False
        # Newest year first, then in term order inside the year.
        ordering = ["-academic_year", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "academic_year", "sequence"],
                name="unique_term_per_branch_year",
            ),
            # Enforced in the database, not just in save(), so a stray script
            # cannot leave a branch with two "current" terms.
            models.UniqueConstraint(
                fields=["branch"],
                condition=models.Q(is_current=True),
                name="one_current_term_per_branch",
            ),
        ]
        indexes = [
            models.Index(fields=["school", "branch"]),
            models.Index(fields=["branch", "academic_year", "sequence"]),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.is_current and self.branch_id:
                # Stand the others down first, or the partial unique constraint
                # rejects this row.
                Term.all_objects.filter(
                    branch_id=self.branch_id, is_current=True
                ).exclude(pk=self.pk).update(is_current=False)
            super().save(*args, **kwargs)


class FeeStructure(BranchScopedModel):
    """The fee set for one class in one term."""

    branch = models.ForeignKey(
        "schools.Branch", on_delete=models.CASCADE, related_name="fee_structures"
    )
    # `class` is a Python keyword, so the field carries the longer name and the
    # verbose name keeps the domain language in the UI.
    school_class = models.ForeignKey(
        "academics.Class",
        on_delete=models.CASCADE,
        related_name="fee_structures",
        verbose_name="class",
    )
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="fee_structures")

    class Meta(BranchScopedModel.Meta):
        abstract = False
        ordering = ["school_class__level", "school_class__year_in_level",
                    "school_class__stream"]
        constraints = [
            models.UniqueConstraint(
                fields=["school_class", "term"],
                name="one_fee_structure_per_class_per_term",
            )
        ]
        indexes = [
            models.Index(fields=["school", "branch"]),
            models.Index(fields=["term", "school_class"]),
        ]

    def __str__(self) -> str:
        return f"{self.school_class} - {self.term}"

    @property
    def total(self) -> Decimal:
        """Sum of the line items. Computed every time, stored never.

        Cheap when the caller has ``prefetch_related("components")``, which the
        list views do.
        """
        return sum((c.amount for c in self.components.all()), Decimal("0"))

    def save(self, *args, **kwargs):
        if self.school_class_id and self.branch_id is None:
            self.branch_id = self.school_class.branch_id
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.school_class_id and self.term_id:
            if self.school_class.branch_id != self.term.branch_id:
                raise ValidationError(
                    {"term": "That term belongs to a different branch than the class."}
                )


class FeeComponent(BranchScopedModel):
    """One line item of a fee structure, e.g. "Textbooks" at 50,000."""

    branch = models.ForeignKey(
        "schools.Branch", on_delete=models.CASCADE, related_name="fee_components"
    )
    fee_structure = models.ForeignKey(
        FeeStructure, on_delete=models.CASCADE, related_name="components"
    )
    name = models.CharField(max_length=120)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    position = models.PositiveSmallIntegerField(
        default=0, help_text="Display order within the structure."
    )

    class Meta(BranchScopedModel.Meta):
        abstract = False
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["fee_structure", "name"],
                name="unique_component_name_per_structure",
            )
        ]
        indexes = [
            models.Index(fields=["school", "branch"]),
            models.Index(fields=["fee_structure", "position"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.amount})"

    def save(self, *args, **kwargs):
        if self.fee_structure_id and self.branch_id is None:
            self.branch_id = self.fee_structure.branch_id
        super().save(*args, **kwargs)
