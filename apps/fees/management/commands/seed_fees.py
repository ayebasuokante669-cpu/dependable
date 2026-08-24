"""Seed a branch's fee structures from ``apps.fees.pricing``.

    python manage.py seed_fees
    python manage.py seed_fees --school "Fulfilled Academy" --branch "Main Campus"

Idempotent: matches on (class, term) and rewrites that structure's line items.
Requires the classes to exist already -- run ``seed_academics`` first.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.academics.models import Class
from apps.fees.models import FeeComponent, FeeStructure, Term
from apps.fees.pricing import FEE_STRUCTURES, TERM, structures_for
from apps.schools.models import Branch, School


class Command(BaseCommand):
    help = "Create the term and per-class fee structures for a branch."

    def add_arguments(self, parser):
        parser.add_argument("--school", default="Fulfilled Academy")
        parser.add_argument("--branch", default="Main Campus")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete fee structures for this term that the pricing file "
            "no longer lists.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        school, branch = self._resolve_target(options)
        term = self._seed_term(school, branch)
        self.stdout.write(f"Seeding {school.name} / {branch.name} / {term.name}\n")

        seeded = self._seed_structures(school, branch, term)

        if options["replace"]:
            self._prune(term, seeded)

        self._report(term)

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

    def _seed_term(self, school, branch) -> Term:
        term, created = Term.all_objects.update_or_create(
            branch=branch,
            academic_year=TERM["academic_year"],
            sequence=TERM["sequence"],
            defaults={
                "school": school,
                "name": TERM["name"],
                # Normally None -- see the comment on TERM in pricing.py.
                "due_date": TERM.get("due_date"),
            },
        )
        if TERM.get("is_current") and not term.is_current:
            term.is_current = True
            term.save()  # save() stands down any other current term first
        self.stdout.write(f"  term: {'created' if created else 'reused'} {term.name}")
        return term

    # -- structures --------------------------------------------------------

    def _seed_structures(self, school, branch, term) -> set[int]:
        classes = list(Class.all_objects.filter(branch=branch))
        created = updated = 0
        seeded: set[int] = set()
        unpriced: list[str] = []

        for klass in classes:
            specs = structures_for(klass)
            if not specs:
                unpriced.append(klass.display_name)
                continue
            if len(specs) > 1:
                raise CommandError(
                    f"{klass.display_name} matches {len(specs)} pricing rules; "
                    f"the fee schedule is ambiguous."
                )

            spec = specs[0]
            # One structure per class, never shared: repricing Primary 3 must
            # not silently move Primary 2 with it.
            structure, made = FeeStructure.all_objects.get_or_create(
                school_class=klass,
                term=term,
                defaults={"school": school, "branch": branch},
            )
            seeded.add(structure.pk)
            created += made
            updated += not made

            # Rewrite the line items so an edited pricing file actually lands.
            structure.components.all().delete()
            FeeComponent.all_objects.bulk_create(
                [
                    FeeComponent(
                        school=school,
                        branch=branch,
                        fee_structure=structure,
                        name=fee_line.name,
                        amount=fee_line.amount,
                        position=position,
                    )
                    for position, fee_line in enumerate(spec.lines)
                ]
            )
            self._check_total(klass, spec, structure)

        self.stdout.write(
            f"  structures: {created} created, {updated} rewritten"
        )
        if unpriced:
            self.stdout.write(
                self.style.WARNING("  no pricing rule for: " + ", ".join(unpriced))
            )
        return seeded

    def _check_total(self, klass, spec, structure) -> None:
        """Compare against the school's own written total, when we have one."""
        if spec.client_total is None:
            return
        actual = structure.total
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

    def _prune(self, term, seeded: set[int]) -> None:
        stale = FeeStructure.all_objects.filter(term=term).exclude(pk__in=seeded)
        labels = [str(s.school_class) for s in stale]
        if labels:
            stale.delete()
            self.stdout.write(
                self.style.WARNING(f"  removed {len(labels)}: " + ", ".join(labels))
            )

    # -- summary -----------------------------------------------------------

    def _report(self, term) -> None:
        structures = (
            FeeStructure.all_objects.filter(term=term)
            .select_related("school_class")
            .prefetch_related("components")
        )
        grand = sum((s.total for s in structures), Decimal("0"))
        self.stdout.write("")
        for structure in structures:
            lines = ", ".join(
                f"{c.name} {c.amount:,.0f}" for c in structure.components.all()
            )
            self.stdout.write(
                f"    {structure.school_class.display_name:16} "
                f"N{structure.total:>10,.0f}   {lines}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {len(structures)} structures, "
                f"{sum(len(s.components.all()) for s in structures)} line items, "
                f"N{grand:,.0f} across the term."
            )
        )
