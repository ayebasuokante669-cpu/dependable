"""Academic setup: the classes a branch runs and the subjects taught in them.

Step 2 of school onboarding. Both models are branch-owned, so they inherit the
tenant scoping automatically -- a principal at one campus never sees another
campus's classes without a single line of filtering in any view.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import BranchScopedModel


class Level(models.IntegerChoices):
    """The bands a class can sit in.

    Values are spaced and ascending so that ordering by ``level`` alone already
    walks the admission ladder -- nursery first, senior secondary last -- and
    leaves room to slot a band in between later without a data migration.
    """

    NURSERY = 10, "Nursery"
    PRIMARY = 20, "Primary"
    JUNIOR_SECONDARY = 30, "Junior Secondary"
    SENIOR_SECONDARY = 40, "Senior Secondary"


class Class(BranchScopedModel):
    """A teaching group: "Pre-KG", "Primary 3", "JSS 1A", "SSS 2 Science".

    ``name`` holds the class without its arm ("SSS 2"); ``stream`` holds the arm
    ("Science", "A") when the school splits that year into more than one group.
    Keeping them apart is what lets every arm of a year sort together and lets a
    subject be attached to one arm but not another.
    """

    branch = models.ForeignKey(
        "schools.Branch", on_delete=models.CASCADE, related_name="classes"
    )
    name = models.CharField(
        max_length=60, help_text='Without the arm, e.g. "JSS 1" or "Primary 3".'
    )
    level = models.PositiveSmallIntegerField(choices=Level.choices)
    year_in_level = models.PositiveSmallIntegerField(
        "year within level",
        default=1,
        help_text="Position inside the level, used for ordering. "
        "Pre-KG is 0, KG 1 is 1, Primary 1 is 1, and so on.",
    )
    stream = models.CharField(
        "stream / arm",
        max_length=40,
        blank=True,
        help_text='Optional, e.g. "Science", "Arts", "A". Leave blank if the '
        "year is a single group.",
    )
    is_active = models.BooleanField(default=True)

    class Meta(BranchScopedModel.Meta):
        abstract = False
        verbose_name = "class"
        verbose_name_plural = "classes"
        # Admission order: band, then year inside the band, then arm.
        ordering = ["level", "year_in_level", "stream", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "name", "stream"],
                name="unique_class_per_branch",
            )
        ]
        indexes = [
            models.Index(fields=["school", "branch"]),
            models.Index(fields=["branch", "level", "year_in_level"]),
        ]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        """How staff say it: "JSS 1A", but "SSS 2 Science".

        A one-character arm reads as a suffix; a word reads as a separate token.
        """
        if not self.stream:
            return self.name
        if len(self.stream) == 1:
            return f"{self.name}{self.stream}"
        return f"{self.name} {self.stream}"

    def clean(self):
        super().clean()
        if self.branch_id and self.school_id:
            if self.branch.school_id != self.school_id:
                raise ValidationError({"branch": "Branch belongs to a different school."})


class Subject(BranchScopedModel):
    """A subject taught at a branch, e.g. "Mathematics", "Basic Science".

    Attached to classes many-to-many, because one subject genuinely spans
    several: Mathematics is taught from Primary 1 through SSS 3, and duplicating
    it per class would mean renaming it eleven times.
    """

    branch = models.ForeignKey(
        "schools.Branch", on_delete=models.CASCADE, related_name="subjects"
    )
    name = models.CharField(max_length=120)
    code = models.CharField(
        max_length=16, blank=True, help_text='Optional short code, e.g. "MTH".'
    )
    classes = models.ManyToManyField(
        Class,
        related_name="subjects",
        blank=True,
        help_text="The classes this subject is taught in.",
    )
    is_active = models.BooleanField(default=True)

    class Meta(BranchScopedModel.Meta):
        abstract = False
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "name"], name="unique_subject_per_branch"
            )
        ]
        indexes = [
            models.Index(fields=["school", "branch"]),
        ]

    def __str__(self) -> str:
        return self.name
