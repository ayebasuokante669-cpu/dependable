"""The student roster: who is enrolled, in which class, and who pays for them.

Step 4 of onboarding. One model, branch-owned like the academic and fee setup,
so a principal's roster is narrowed to their own campus without any view here
filtering for it.

What is deliberately *not* on this model is money. A student's expected fee is
the total of their class's ``FeeStructure`` for the current term, derived live
in :mod:`apps.students.fees`. Copying that figure onto the student would be a
second source of truth that goes stale the first time a class is repriced, and
would have to be corrected for every student in the class by hand.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Upper
from django.urls import reverse
from django.utils import timezone

from apps.core.models import BranchScopedModel

from .validators import format_phone, normalise_admission_number, validate_phone


class Sex(models.TextChoices):
    MALE = "male", "Male"
    FEMALE = "female", "Female"


class StudentStatus(models.TextChoices):
    """Where a student stands with the school.

    Only ``ACTIVE`` students are billed, which is why leaving is recorded as a
    status rather than a deletion: a withdrawn student's history has to survive,
    or last term's payments lose the person they were made for.
    """

    ACTIVE = "active", "Active"
    INACTIVE = "inactive", "Inactive"
    GRADUATED = "graduated", "Graduated"
    WITHDRAWN = "withdrawn", "Withdrawn"


#: The statuses that mean "still on the roll", used by the list's default view
#: and by anything that counts who is being charged this term.
ENROLLED_STATUSES = (StudentStatus.ACTIVE,)


class Student(BranchScopedModel):
    """One enrolled child, their class, and their parent/guardian contact."""

    branch = models.ForeignKey(
        "schools.Branch", on_delete=models.CASCADE, related_name="students"
    )
    # `class` is a Python keyword, so the field carries the longer name and the
    # verbose name keeps the domain language in the UI -- the same trade
    # FeeStructure makes.
    school_class = models.ForeignKey(
        "academics.Class",
        # A class with students on it must not vanish underneath them: the
        # roster would keep the rows but lose what they were enrolled in.
        on_delete=models.PROTECT,
        related_name="students",
        verbose_name="class",
    )

    admission_number = models.CharField(
        max_length=32,
        help_text='The school\'s own number, e.g. "FA/2025/001". Unique within '
        "this branch.",
    )
    first_name = models.CharField(max_length=80)
    last_name = models.CharField("surname", max_length=80)
    other_names = models.CharField(max_length=120, blank=True)
    sex = models.CharField(max_length=10, choices=Sex.choices)
    date_of_birth = models.DateField(null=True, blank=True)
    date_admitted = models.DateField(default=timezone.localdate)
    status = models.CharField(
        max_length=20, choices=StudentStatus.choices, default=StudentStatus.ACTIVE
    )

    # --- Parent / guardian -------------------------------------------------
    # Flat fields on the student, not a separate Guardian model. One contact per
    # student is what the school's own records hold today, and a shared-guardian
    # model that nobody has the data to populate would be scaffolding pretending
    # to be a feature. Splitting siblings onto a shared guardian later is a
    # migration, not a redesign.
    parent_name = models.CharField("parent / guardian name", max_length=160)
    parent_phone = models.CharField(
        "parent / guardian phone",
        max_length=20,
        validators=[validate_phone],
        help_text="Mobile number, e.g. 0803 123 4567.",
    )
    parent_email = models.EmailField("parent / guardian email", blank=True)
    address = models.TextField(blank=True)

    class Meta(BranchScopedModel.Meta):
        abstract = False
        # Class order first (the admission ladder), then alphabetically by
        # surname -- how a class register is read.
        ordering = [
            "school_class__level",
            "school_class__year_in_level",
            "school_class__stream",
            "last_name",
            "first_name",
        ]
        constraints = [
            # Per branch, not globally: two campuses of the same school both
            # number their intake from 001, and neither should have to renumber
            # because the other got there first.
            models.UniqueConstraint(
                Upper("admission_number"),
                "branch",
                name="unique_admission_number_per_branch",
                violation_error_message=(
                    "Another student at this branch already has that admission "
                    "number."
                ),
            )
        ]
        indexes = [
            models.Index(fields=["school", "branch"]),
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["school_class", "status"]),
            models.Index(fields=["last_name", "first_name"]),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.admission_number})"

    def get_absolute_url(self) -> str:
        return reverse("students:student_detail", args=[self.pk])

    # -- names --------------------------------------------------------------

    @property
    def full_name(self) -> str:
        """"Chinaza Okonkwo" -- what a staff member says out loud."""
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def formal_name(self) -> str:
        """"Okonkwo, Chinaza Adaeze" -- how the register and reports print it."""
        given = " ".join(part for part in (self.first_name, self.other_names) if part)
        return f"{self.last_name}, {given}".strip().rstrip(",")

    @property
    def initials(self) -> str:
        first = self.first_name[:1].upper()
        last = self.last_name[:1].upper()
        return f"{first}{last}" or "?"

    # -- derived facts -------------------------------------------------------

    @property
    def age(self) -> int | None:
        """Whole years today, or ``None`` when no date of birth was recorded."""
        if not self.date_of_birth:
            return None
        today = timezone.localdate()
        had_birthday = (today.month, today.day) >= (
            self.date_of_birth.month,
            self.date_of_birth.day,
        )
        return today.year - self.date_of_birth.year - (0 if had_birthday else 1)

    @property
    def is_enrolled(self) -> bool:
        return self.status in ENROLLED_STATUSES

    @property
    def parent_phone_display(self) -> str:
        return format_phone(self.parent_phone)

    @property
    def parent_phone_link(self) -> str:
        """``tel:`` target -- staff read the roster on a phone as often as a desk."""
        return f"tel:+234{self.parent_phone.lstrip('0')}" if self.parent_phone else ""

    # -- persistence ---------------------------------------------------------

    def save(self, *args, **kwargs):
        # A platform owner has no school of their own, so the class -- which
        # always knows its branch -- is what places the student.
        if self.school_class_id and self.branch_id is None:
            self.branch_id = self.school_class.branch_id
        self.admission_number = normalise_admission_number(self.admission_number)
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        self.admission_number = normalise_admission_number(self.admission_number)
        if self.school_class_id and self.branch_id:
            if self.school_class.branch_id != self.branch_id:
                raise ValidationError(
                    {"school_class": "That class belongs to a different branch."}
                )
        if self.date_of_birth and self.date_admitted:
            if self.date_of_birth > self.date_admitted:
                raise ValidationError(
                    {"date_of_birth": "A student cannot be born after being admitted."}
                )
        if self.date_of_birth and self.date_of_birth > timezone.localdate():
            raise ValidationError({"date_of_birth": "That date is in the future."})
