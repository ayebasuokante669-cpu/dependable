"""Seed a branch's admission fee schedules from ``apps.admissions.admission_pricing``.

    python manage.py seed_admission_fees
    python manage.py seed_admission_fees --school "Fulfilled Academy" --branch "Main Campus"

Idempotent: matches on (class, academic year) and rewrites that schedule's line
items. Requires the classes to exist already -- run ``seed_academics`` first.

**This command never touches ``fees.FeeStructure``.** Intake money and termly
money are separate models with separate seeders, and running this one cannot
change what a class owes per term. That separation is asserted in the tests, so
a future refactor that quietly merges them fails the suite rather than a
school's invoices.

A schedule whose sheet still has unpriced lines is *skipped and named*, not
seeded with zeros. See the note at the top of ``admission_pricing.py``: a
real-looking 0 in front of a parent is worse than a command that says which
figure it is waiting for.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.academics.models import Class
from apps.admissions.admission_pricing import ACADEMIC_YEAR, specs_for
from apps.admissions.models import AdmissionFeeItem, AdmissionFeeSchedule
from apps.schools.models import Branch, School

ZERO = Decimal("0")


class Command(BaseCommand):
    help = "Create the per-class admission (intake) fee schedules for a branch."

    def add_arguments(self, parser):
        parser.add_argument("--school", default="Fulfilled Academy")
        parser.add_argument("--branch", default="Main Campus")
        parser.add_argument(
            "--year",
            default=ACADEMIC_YEAR,
            help=f"Intake year to price. Defaults to {ACADEMIC_YEAR}.",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete schedules for this year that the pricing file no "
            "longer lists.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        school, branch = self._resolve_target(options)
        year = options["year"]
        self.stdout.write(
            f"Seeding admission fees for {school.name} / {branch.name} / {year}\n"
        )

        seeded, skipped = self._seed_schedules(school, branch, year)

        if options["replace"]:
            self._prune(branch, year, seeded)

        self._report(branch, year, skipped)

    # -- target -----------------------------------------------------------

    def _resolve_target(self, options):
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

    # -- schedules ---------------------------------------------------------

    def _seed_schedules(self, school, branch, year) -> tuple[set[int], list[str]]:
        classes = list(Class.all_objects.filter(branch=branch))
        created = updated = 0
        seeded: set[int] = set()
        unpriced: list[str] = []
        skipped: list[str] = []

        for klass in classes:
            specs = specs_for(klass)
            if not specs:
                unpriced.append(klass.display_name)
                continue
            if len(specs) > 1:
                raise CommandError(
                    f"{klass.display_name} matches {len(specs)} admission fee "
                    f"rules; the sheet is ambiguous."
                )

            spec = specs[0]
            if not spec.is_complete:
                # Named, not guessed. The report at the end lists exactly which
                # figures are outstanding.
                missing = ", ".join(item.name for item in spec.pending_lines)
                skipped.append(f"{klass.display_name}: {missing}")
                continue

            # One schedule per class, never shared: repricing Primary 3's intake
            # must not silently move Primary 2 with it.
            schedule, made = AdmissionFeeSchedule.all_objects.get_or_create(
                school_class=klass,
                academic_year=year,
                defaults={"school": school, "branch": branch},
            )
            seeded.add(schedule.pk)
            created += made
            updated += not made

            # Rewrite the line items so an edited sheet actually lands.
            schedule.items.all().delete()
            AdmissionFeeItem.all_objects.bulk_create(
                [
                    AdmissionFeeItem(
                        school=school,
                        branch=branch,
                        schedule=schedule,
                        name=item.name,
                        amount=item.amount,
                        kind=item.kind,
                        position=position,
                    )
                    for position, item in enumerate(spec.lines)
                ]
            )
            self._check_total(klass, spec, schedule)

        self.stdout.write(f"  schedules: {created} created, {updated} rewritten")
        if unpriced:
            self.stdout.write(
                self.style.WARNING(
                    "  no admission fee rule for: " + ", ".join(unpriced)
                )
            )
        return seeded, skipped

    def _check_total(self, klass, spec, schedule) -> None:
        """Compare against the school's own written total, when we have one.

        Identical handling to the termly seeder's Primary 1 case: the line
        items are charged, the gap is reported, and no balancing line is
        invented to make the arithmetic tidy.
        """
        if spec.client_total is None:
            return
        actual = schedule.compulsory_total
        if actual == spec.client_total:
            return
        gap = spec.client_total - actual
        self.stdout.write(
            self.style.WARNING(
                f"\n  ! {klass.display_name}: line items sum to N{actual:,.0f} but "
                f"the school's written total is N{spec.client_total:,.0f} "
                f"(difference N{gap:,.0f})."
            )
        )
        if spec.total_note:
            self.stdout.write(self.style.WARNING(f"    {spec.total_note}\n"))

    def _prune(self, branch, year, seeded: set[int]) -> None:
        stale = AdmissionFeeSchedule.all_objects.filter(
            branch=branch, academic_year=year
        ).exclude(pk__in=seeded)
        labels = [str(s.school_class) for s in stale]
        if labels:
            stale.delete()
            self.stdout.write(
                self.style.WARNING(f"  removed {len(labels)}: " + ", ".join(labels))
            )

    # -- summary -----------------------------------------------------------

    def _report(self, branch, year, skipped: list[str]) -> None:
        schedules = (
            AdmissionFeeSchedule.all_objects.filter(branch=branch, academic_year=year)
            .select_related("school_class")
            .prefetch_related("items")
        )
        self.stdout.write("")
        for schedule in schedules:
            lines = ", ".join(
                f"{item.name} {item.amount:,.0f}" for item in schedule.items.all()
            )
            self.stdout.write(
                f"    {schedule.school_class.display_name:16} "
                f"intake N{schedule.compulsory_total:>10,.0f}  "
                f"books N{schedule.books_total:>9,.0f}   {lines}"
            )

        if skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"\n  {len(skipped)} class"
                    f"{'es' if len(skipped) != 1 else ''} skipped -- the sheet's "
                    f"figures have not been supplied yet:"
                )
            )
            for row in skipped:
                self.stdout.write(self.style.WARNING(f"    - {row}"))
            self.stdout.write(
                self.style.WARNING(
                    "\n  Fill the amounts into apps/admissions/admission_pricing.py "
                    "(replace each PENDING with naira(...)) and re-run. Nothing "
                    "is seeded as zero on purpose -- see the note at the top of "
                    "that file."
                )
            )

        count = len(schedules)
        if count:
            grand = sum((s.compulsory_total for s in schedules), ZERO)
            books = sum((s.books_total for s in schedules), ZERO)
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nDone. {count} admission fee schedule"
                    f"{'s' if count != 1 else ''}, "
                    f"N{grand:,.0f} at intake plus N{books:,.0f} in books.\n"
                    f"The termly fee structures were not touched."
                )
            )
        elif not skipped:
            self.stdout.write(self.style.WARNING("\nNothing to seed."))
