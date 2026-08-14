"""Seed a branch's academic setup from ``apps.academics.curriculum``.

    python manage.py seed_academics
    python manage.py seed_academics --school "Fulfilled Academy" --branch "Main Campus"

Idempotent: re-running matches on (branch, name) and updates rather than
duplicating.  Pass ``--replace-subjects`` after editing the curriculum module to
also delete subjects that are no longer listed there.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.academics.curriculum import CLASSES, SUBJECTS, classes_for
from apps.academics.models import Class, Subject
from apps.schools.models import Branch, Plan, School, SchoolStatus


class Command(BaseCommand):
    help = "Create the class ladder and subject list for a branch."

    def add_arguments(self, parser):
        parser.add_argument("--school", default="Fulfilled Academy")
        parser.add_argument("--branch", default="Main Campus")
        parser.add_argument(
            "--create-school",
            action="store_true",
            help="Create the school/branch if they do not exist yet.",
        )
        parser.add_argument(
            "--replace-subjects",
            action="store_true",
            help="Delete subjects at this branch that the curriculum no longer lists.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        school, branch = self._resolve_target(options)
        self.stdout.write(f"Seeding {school.name} / {branch.name}\n")

        classes = self._seed_classes(school, branch)
        self._seed_subjects(school, branch, classes)

        if options["replace_subjects"]:
            self._prune_subjects(branch)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {Class.all_objects.filter(branch=branch).count()} classes, "
                f"{Subject.all_objects.filter(branch=branch).count()} subjects."
            )
        )

    # -- target -----------------------------------------------------------

    def _resolve_target(self, options):
        """Find the school and branch, using unscoped managers.

        Management commands run with no tenant context, so the scoped managers
        would not filter anyway -- ``all_objects`` just says so out loud.
        """
        name, branch_name = options["school"], options["branch"]

        school = School.all_objects.filter(name=name).first()
        if school is None:
            if not options["create_school"]:
                available = ", ".join(
                    School.all_objects.values_list("name", flat=True)
                ) or "none"
                raise CommandError(
                    f'No school named "{name}". Pass --create-school to make it, '
                    f"or pick an existing one (available: {available})."
                )
            school = School.all_objects.create(
                name=name, plan=Plan.GROWTH, status=SchoolStatus.ACTIVE
            )
            self.stdout.write(f"  created school {school.name}")

        branch = Branch.all_objects.filter(school=school, name=branch_name).first()
        if branch is None:
            if not options["create_school"]:
                raise CommandError(
                    f'No branch named "{branch_name}" at {school.name}. '
                    f"Pass --create-school to make it."
                )
            branch = Branch.all_objects.create(school=school, name=branch_name)
            self.stdout.write(f"  created branch {branch.name}")

        return school, branch

    # -- classes ----------------------------------------------------------

    def _seed_classes(self, school, branch) -> list[Class]:
        created = updated = 0
        result: list[Class] = []

        for spec in CLASSES:
            # A spec with no streams is one class; with streams, one per arm.
            for stream in spec.streams or ("",):
                klass, made = Class.all_objects.update_or_create(
                    branch=branch,
                    name=spec.name,
                    stream=stream,
                    defaults={
                        "school": school,
                        "level": spec.level,
                        "year_in_level": spec.year_in_level,
                        "is_active": True,
                    },
                )
                result.append(klass)
                created += made
                updated += not made

        self.stdout.write(f"  classes:  {created} created, {updated} already present")
        return result

    # -- subjects ---------------------------------------------------------

    def _seed_subjects(self, school, branch, classes: list[Class]) -> None:
        created = updated = 0
        unplaced: list[str] = []

        for spec in SUBJECTS:
            subject, made = Subject.all_objects.update_or_create(
                branch=branch,
                name=spec.name,
                defaults={"school": school, "code": spec.code, "is_active": True},
            )
            targets = classes_for(spec, classes)
            subject.classes.set(targets)
            if not targets:
                unplaced.append(spec.name)
            created += made
            updated += not made

        self.stdout.write(f"  subjects: {created} created, {updated} already present")
        if unplaced:
            # Usually a stream name in the curriculum that no class actually uses.
            self.stdout.write(
                self.style.WARNING(
                    "  no matching class for: " + ", ".join(unplaced)
                )
            )

    def _prune_subjects(self, branch) -> None:
        keep = {spec.name for spec in SUBJECTS}
        stale = Subject.all_objects.filter(branch=branch).exclude(name__in=keep)
        names = list(stale.values_list("name", flat=True))
        if names:
            stale.delete()
            self.stdout.write(
                self.style.WARNING(f"  removed {len(names)}: " + ", ".join(names))
            )
