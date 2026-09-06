"""Close enquiries whose validity window has run out.

    python manage.py expire_enquiries
    python manage.py expire_enquiries --dry-run
    python manage.py expire_enquiries --school "Fulfilled Academy"

Meant for a nightly cron. Two things it deliberately does not do:

**It does not delete anything.** An expired enquiry keeps its row and its
history. "How many enquiries did that Instagram post bring us, and how many
converted?" is a question a school asks in March about a January post, and it
is unanswerable if the ones that lapsed have been swept off the table.

**It only expires enquiries.** Once a family has filled in the application the
window has done its job; expiring somebody mid-assessment would be the platform
closing a door the school is holding open. The queryset is
``Applicant.all_objects.lapsed()``, which encodes exactly that.

Runs unscoped on purpose -- there is no signed-in user in a cron job, and a
scoped manager would see, and therefore expire, nothing.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.admissions.models import Applicant, ApplicantStatus
from apps.schools.models import School


class Command(BaseCommand):
    help = "Mark enquiries past their validity window as expired."

    def add_arguments(self, parser):
        parser.add_argument(
            "--school",
            default="",
            help="Limit to one school by name. Default: every school.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be expired without writing anything.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        # all_objects: a cron job has no tenant context, and the scoped
        # manager would find nothing to expire.
        queryset = Applicant.all_objects.lapsed(now).select_related("school", "branch")

        if options["school"]:
            school = School.all_objects.filter(name=options["school"]).first()
            if school is None:
                self.stdout.write(
                    self.style.WARNING(f'No school named "{options["school"]}".')
                )
                return
            queryset = queryset.filter(school=school)

        lapsed = list(queryset)
        if not lapsed:
            self.stdout.write("Nothing has lapsed.")
            return

        for applicant in lapsed:
            self.stdout.write(
                f"  {applicant.reference:16} {applicant.full_name:28} "
                f"{applicant.school.name} / {applicant.branch.name} "
                f"(expired {applicant.expires_at:%Y-%m-%d})"
            )

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING(
                    f"\nDry run: {len(lapsed)} enquir"
                    f"{'y' if len(lapsed) == 1 else 'ies'} would be expired."
                )
            )
            return

        with transaction.atomic():
            # One UPDATE rather than a save() each: nothing on the model needs
            # to fire, and a school with a busy intake can lapse hundreds.
            count = Applicant.all_objects.filter(
                pk__in=[a.pk for a in lapsed]
            ).update(
                status=ApplicantStatus.EXPIRED,
                expired_at=now,
                updated_at=now,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nExpired {count} enquir{'y' if count == 1 else 'ies'}. "
                f"Every row is kept -- nothing was deleted."
            )
        )
