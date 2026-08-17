"""Seed a branch's student roster from ``apps.students.roster``.

    python manage.py seed_students
    python manage.py seed_students --school "Fulfilled Academy" --branch "Main Campus"

Idempotent: matches on (branch, admission number) and updates that student.
Requires the classes to exist already -- run ``seed_academics`` first, and
``seed_fees`` if the roster screens should show a fee position rather than
"no fees set".
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.academics.models import Class
from apps.fees.models import FeeStructure, Term
from apps.schools.models import Branch, School
from apps.students.models import Student, StudentStatus
from apps.students.roster import ROSTERS
from apps.students.validators import validate_phone


class Command(BaseCommand):
    help = "Create the pilot student roster for a branch."

    def add_arguments(self, parser):
        parser.add_argument("--school", default="Fulfilled Academy")
        parser.add_argument("--branch", default="Main Campus")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete students at this branch that the roster file no longer "
            "lists.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        school, branch = self._resolve_target(options)
        self.stdout.write(f"Seeding {school.name} / {branch.name}\n")

        seeded = self._seed_students(school, branch)

        if options["replace"]:
            self._prune(branch, seeded)

        self._report(branch)

    # -- target -----------------------------------------------------------

    def _resolve_target(self, options):
        """Find the school and branch with the unscoped managers.

        Management commands run with no tenant context, so the scoped managers
        would not filter anyway -- ``all_objects`` just says so out loud.
        """
        school = School.all_objects.filter(name=options["school"]).first()
        if school is None:
            raise CommandError(
                f'No school named "{options["school"]}". '
                f"Run seed_academics --create-school first."
            )
        branch = Branch.all_objects.filter(school=school, name=options["branch"]).first()
        if branch is None:
            raise CommandError(
                f'No branch named "{options["branch"]}" at {school.name}.'
            )
        if not Class.all_objects.filter(branch=branch).exists():
            raise CommandError(
                f"{branch.name} has no classes yet. Run seed_academics first."
            )
        return school, branch

    def _resolve_class(self, branch, roster) -> Class:
        klass = Class.all_objects.filter(
            branch=branch, name=roster.class_name, stream=roster.stream
        ).first()
        if klass is None:
            label = f"{roster.class_name} {roster.stream}".strip()
            raise CommandError(
                f'{branch.name} has no class "{label}". The roster and the '
                f"curriculum have drifted apart."
            )
        return klass

    # -- students ----------------------------------------------------------

    def _seed_students(self, school, branch) -> set[int]:
        created = updated = 0
        seeded: set[int] = set()

        for roster in ROSTERS:
            klass = self._resolve_class(branch, roster)
            for spec in roster.students:
                # Validated here rather than trusted: the roster file is edited
                # by hand, and a typo in a parent's number is only noticed at
                # the moment someone needs to call them.
                try:
                    validate_phone(spec.parent_phone)
                except ValidationError:
                    raise CommandError(
                        f"{spec.admission_number} ({spec.first_name} "
                        f"{spec.last_name}) has an invalid parent phone number: "
                        f"{spec.parent_phone!r}."
                    )

                student, made = Student.all_objects.update_or_create(
                    branch=branch,
                    admission_number=spec.admission_number,
                    defaults={
                        "school": school,
                        "school_class": klass,
                        "first_name": spec.first_name,
                        "last_name": spec.last_name,
                        "other_names": spec.other_names,
                        "sex": spec.sex,
                        "date_of_birth": spec.date_of_birth,
                        "date_admitted": spec.date_admitted,
                        "status": spec.status,
                        "parent_name": spec.parent_name,
                        "parent_phone": spec.parent_phone,
                        "parent_email": spec.parent_email,
                        "address": spec.address,
                    },
                )
                seeded.add(student.pk)
                created += made
                updated += not made

        self.stdout.write(f"  students: {created} created, {updated} updated")
        return seeded

    def _prune(self, branch, seeded: set[int]) -> None:
        stale = Student.all_objects.filter(branch=branch).exclude(pk__in=seeded)
        labels = [f"{s.admission_number} {s.full_name}" for s in stale]
        if labels:
            stale.delete()
            self.stdout.write(
                self.style.WARNING(f"  removed {len(labels)}: " + ", ".join(labels))
            )

    # -- summary -----------------------------------------------------------

    def _report(self, branch) -> None:
        students = list(
            Student.all_objects.filter(branch=branch).select_related("school_class")
        )
        by_class = Counter(s.school_class.display_name for s in students)
        by_status = Counter(s.get_status_display() for s in students)

        term = Term.all_objects.filter(branch=branch, is_current=True).first()
        totals = {}
        if term is not None:
            totals = {
                structure.school_class_id: structure.total
                for structure in FeeStructure.all_objects.filter(
                    term=term
                ).prefetch_related("components")
            }

        self.stdout.write("")
        expected = Decimal("0")
        unpriced = 0
        for klass_name, count in sorted(by_class.items()):
            self.stdout.write(f"    {klass_name:16} {count:>3} student(s)")
        for student in students:
            if student.status != StudentStatus.ACTIVE:
                continue
            if student.school_class_id in totals:
                expected += totals[student.school_class_id]
            else:
                unpriced += 1

        status_line = ", ".join(f"{count} {label.lower()}"
                                for label, count in sorted(by_status.items()))
        self.stdout.write("")
        self.stdout.write(f"  status: {status_line}")
        if term is None:
            self.stdout.write(
                self.style.WARNING(
                    "  no current term at this branch, so no fees are expected "
                    "yet. Run seed_fees."
                )
            )
        else:
            self.stdout.write(
                f"  expected for {term.name}: N{expected:,.0f} "
                f"across the active roster"
            )
            if unpriced:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {unpriced} active student(s) are in a class with no "
                        f"fee structure for this term."
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {len(students)} students across {len(by_class)} classes."
            )
        )
