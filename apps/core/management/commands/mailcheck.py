"""Find out why an email did not arrive, from the machine that was supposed to send it.

The welcome email is best effort by design: a send that fails must not turn a
completed signup into an error page. The cost of that is a silent failure, and
"it isn't arriving" is then a question nobody on the outside can answer.

This answers it. It prints what the deployment is actually configured to do, and
then -- with ``--to`` -- sends a real message through the real backend and reports
exactly what came back, including the provider's own error rather than a swallowed
one.

    python manage.py mailcheck
    python manage.py mailcheck --to you@example.com
    python manage.py mailcheck --to you@example.com --welcome

Nothing here is destructive and nothing is written to the database. ``--welcome``
renders the genuine welcome email against a throwaway school so what lands in the
inbox is the message a new owner would get, logo and all.
"""

from __future__ import annotations

import traceback

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.management.base import BaseCommand, CommandError

TICK = "ok   "
CROSS = "FAIL "
NOTE = "     "


class Command(BaseCommand):
    help = "Report how mail is configured, and optionally send a test message."

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            help="Send a test message to this address. Omit for a dry report.",
        )
        parser.add_argument(
            "--welcome",
            action="store_true",
            help="Send the real welcome email rather than a plain test, so the "
                 "template, the logo and the links are all exercised.",
        )

    # -- the report --------------------------------------------------------

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("How this deployment sends mail"))
        ok = self.report()

        recipient = options.get("to")
        if not recipient:
            self.stdout.write("")
            self.stdout.write(
                "Nothing sent. Add --to you@example.com to send a real message "
                "and see what the provider says."
            )
            return

        if not ok:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Sending anyway, so the error comes from the provider rather "
                "than from a guess."
            ))

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(f"Sending to {recipient}"))
        if options["welcome"]:
            self.send_welcome(recipient)
        else:
            self.send_plain(recipient)

    def line(self, state, label, value=""):
        self.stdout.write(f"  {state}{label}" + (f": {value}" if value else ""))

    def report(self) -> bool:
        ok = True

        backend = getattr(settings, "EMAIL_BACKEND", "")
        self.line(NOTE, "EMAIL_BACKEND", backend)
        if "console" in backend:
            self.line(
                NOTE,
                "note",
                "the console backend prints mail instead of sending it, so "
                "nothing will reach an inbox from here",
            )
        elif "locmem" in backend:
            self.line(NOTE, "note", "the in-memory backend discards mail")

        if "anymail" in backend:
            key = (getattr(settings, "ANYMAIL", {}) or {}).get("RESEND_API_KEY", "")
            if key.strip():
                self.line(TICK, "RESEND_API_KEY", f"set ({len(key)} characters)")
            else:
                self.line(CROSS, "RESEND_API_KEY", "empty -- every send will fail")
                ok = False

        sender = getattr(settings, "DEFAULT_FROM_EMAIL", "")
        self.line(TICK if "@" in sender else CROSS, "DEFAULT_FROM_EMAIL", sender)
        if "@" in sender:
            domain = sender[sender.rfind("@") + 1:].rstrip(">").strip()
            self.line(
                NOTE,
                "sender domain",
                f"{domain} -- Resend refuses to send from a domain it has not "
                f"verified, and that refusal is the commonest reason a message "
                f"never arrives",
            )
        else:
            ok = False

        base = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip()
        if base:
            self.line(TICK, "PUBLIC_BASE_URL", base)
        else:
            self.line(
                CROSS,
                "PUBLIC_BASE_URL",
                "empty -- the welcome email is skipped entirely, because every "
                "link in it would be relative",
            )
            ok = False

        self.line(NOTE, "DEBUG", str(settings.DEBUG))
        return ok

    # -- the sends ---------------------------------------------------------

    def send_plain(self, recipient: str):
        message = EmailMultiAlternatives(
            subject="SCHOOLCORD mail check",
            body=(
                "If you are reading this, mail from this deployment reaches "
                "you.\n\nSent by `manage.py mailcheck`."
            ),
            to=[recipient],
        )
        self.deliver(message)

    def send_welcome(self, recipient: str):
        """The genuine welcome email, rendered against a throwaway school.

        Nothing is saved: the School, Branch and User are unsaved instances, which
        is all the template reads. So this exercises the real template, the real
        absolute URLs and the real logo without leaving a school behind.
        """
        from django.contrib.auth import get_user_model

        from apps.core.emails import send_welcome_email
        from apps.schools.models import Branch, School

        school = School(name="Mailcheck Academy", slug="mailcheck-academy")
        branch = Branch(school=school, name="Main Campus")
        owner = get_user_model()(
            username="mailcheck", email=recipient,
            first_name="Mail", last_name="Check",
        )
        try:
            sent = send_welcome_email(school, branch, owner)
        except Exception:
            self.stdout.write(self.style.ERROR("  The send raised:"))
            self.stdout.write(traceback.format_exc())
            raise CommandError("The welcome email could not be sent.")

        if sent:
            self.stdout.write(self.style.SUCCESS(
                "  Sent. It is handed to the provider -- check the inbox, and "
                "the spam folder."
            ))
        else:
            raise CommandError(
                "send_welcome_email returned False, so it declined to send. The "
                "report above says why -- almost always PUBLIC_BASE_URL. The "
                "reason is also logged at WARNING by apps.core.emails."
            )

    def deliver(self, message):
        try:
            # A connection of our own with fail_silently off, so the provider's
            # error surfaces here instead of being counted as "0 sent".
            message.connection = get_connection(fail_silently=False)
            sent = message.send()
        except Exception:
            self.stdout.write(self.style.ERROR("  The send raised:"))
            self.stdout.write(traceback.format_exc())
            raise CommandError("Mail could not be sent. The traceback is above.")

        if sent:
            self.stdout.write(self.style.SUCCESS(
                f"  Handed {sent} message to the backend. Check the inbox, and "
                f"the spam folder."
            ))
        else:
            raise CommandError(
                "The backend accepted the call but sent nothing. With Anymail "
                "that usually means the API rejected the recipient -- an "
                "unverified sender domain, or a sandbox that only allows the "
                "account's own address."
            )
