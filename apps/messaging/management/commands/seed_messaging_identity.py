"""Register a school's Sender ID.

    python manage.py seed_messaging_identity
    python manage.py seed_messaging_identity --school "Dap Group of Schools" \\
        --sender-id Dapgroup --provider bulksmsnigeria --approve

Idempotent: matches on the school's default (school-wide) config and updates it
rather than creating a second one.

Defaults to the pilot school, Fulfilled Academy, sending as "Fulfilled" through
BulkSMS Nigeria on the platform's master account. ``--approve`` is on by
default because a seeded school that cannot send is a demo that stops at the
first Send button; pass ``--pending`` to seed the not-yet-approved state
instead, which is what a real school looks like on day one.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.messaging.models import SchoolMessagingConfig, SenderIdStatus
from apps.messaging.providers import PROVIDERS, ProviderKey
from apps.messaging.validators import normalise_sender_id, validate_sender_id
from apps.schools.models import School

#: The pilot school and the name its parents will see.
DEFAULT_SCHOOL = "Fulfilled Academy"
DEFAULT_SENDER_ID = "Fulfilled"
DEFAULT_PROVIDER = ProviderKey.BULKSMSNIGERIA


class Command(BaseCommand):
    help = "Register and approve a school's SMS Sender ID."

    def add_arguments(self, parser):
        parser.add_argument("--school", default=DEFAULT_SCHOOL)
        parser.add_argument(
            "--sender-id",
            default=DEFAULT_SENDER_ID,
            help="The alphanumeric name parents see. At most 11 characters.",
        )
        parser.add_argument(
            "--provider",
            default=DEFAULT_PROVIDER,
            choices=sorted(PROVIDERS),
            help="The gateway this Sender ID is registered with.",
        )
        parser.add_argument(
            "--api-key",
            default="",
            help="The school's own gateway key. Blank means the platform's "
            "master account sends on its behalf.",
        )
        parser.add_argument(
            "--pending",
            action="store_true",
            help="Leave it awaiting approval, so the blocked-sending path is "
            "visible on the screens.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        school = School.all_objects.filter(name=options["school"]).first()
        if school is None:
            raise CommandError(
                f'No school named "{options["school"]}". '
                f"Run seed_academics --create-school first."
            )

        sender_id = normalise_sender_id(options["sender_id"])
        try:
            validate_sender_id(sender_id)
        except Exception as exc:
            # A bad Sender ID here is a typo on the command line, and the
            # validator's sentence says exactly what is wrong with it.
            raise CommandError(str(exc)) from None

        clash = (
            SchoolMessagingConfig.all_objects.filter(sender_id__iexact=sender_id)
            .exclude(school=school)
            .select_related("school")
            .first()
        )
        if clash is not None:
            raise CommandError(
                f'"{sender_id}" is already registered to {clash.school.name}. '
                f"Two schools cannot send under the same name."
            )

        status = (
            SenderIdStatus.PENDING if options["pending"] else SenderIdStatus.APPROVED
        )
        config, created = SchoolMessagingConfig.all_objects.update_or_create(
            school=school,
            branch=None,
            defaults={
                "sender_id": sender_id,
                "provider": options["provider"],
                "api_key": options["api_key"],
                "status": status,
            },
        )
        if status == SenderIdStatus.APPROVED and config.approved_at is None:
            # Seeded approvals have no human behind them, which the null
            # approved_by records honestly.
            config.approve(by=None)

        self.stdout.write(
            f"  {'created' if created else 'updated'} {school.name}: "
            f"{config.sender_id} via {config.get_provider_display()}"
        )
        self.stdout.write(
            f"  account: "
            f"{'own key' if config.uses_own_credentials else 'platform master'}"
        )

        if config.is_usable:
            self.stdout.write(
                self.style.SUCCESS(
                    f'  approved — parents will see messages from "{config.sender_id}"'
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"  {config.get_status_display().lower()} — nothing will send "
                    f"under it until it is approved"
                )
            )
