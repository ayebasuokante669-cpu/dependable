"""What the parent receives: the acknowledgement, the nudge, and the decision.

Three emails, and one rule behind all of them -- **the school writes, not the
platform**. A parent who found the school on Instagram has never heard of
SCHOOLCORD, and an email signed by a piece of software they did not know their
child's school used is at best confusing and at worst reads as a scam. So the
subject, the body and the signature are the school's; the platform appears
nowhere in what is sent.

The one place it cannot be hidden is the envelope sender. Mail sent as
``@theschool.com`` from our servers would fail that domain's SPF and land in
spam, which helps nobody. So the ``From:`` address stays the platform's while
its *display name* is the school, and ``Reply-To:`` is the school's own office
address -- a parent hitting reply reaches the school, which is the part that
actually matters to them.

Every function returns ``True`` only if a message was actually handed to the
mail backend, and none of them raises on a missing address: an enquiry taken
over the counter from a parent with no email is a perfectly good enquiry, not
an error for the front desk to deal with.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import Applicant, ApplicantStatus

logger = logging.getLogger(__name__)


def _from_header(school) -> str:
    """``"Fulfilled Academy" <no-reply@schoolcord.app>`` -- see the module docstring."""
    address = settings.DEFAULT_FROM_EMAIL
    # DEFAULT_FROM_EMAIL is usually already "Name <addr>"; take the address.
    if "<" in address and ">" in address:
        address = address[address.index("<") + 1 : address.index(">")]
    name = (school.name or "").replace('"', "").strip()
    return f'"{name}" <{address}>' if name else address


def public_enquiry_url(school) -> str:
    """An absolute link back to the school's own enquiry page.

    Absolute because an email has no request to resolve a relative path
    against. ``PUBLIC_BASE_URL`` is the deployment's own origin; where it has
    not been set the link is simply omitted rather than sent as a broken one.
    """
    base = (getattr(settings, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    if not base:
        return ""
    return base + reverse("admissions_public:public_enquiry", args=[school.slug])


def _context(applicant: Applicant) -> dict:
    school = applicant.school
    return {
        "applicant": applicant,
        "school": school,
        "branch": applicant.branch,
        "reference": applicant.reference,
        "child": applicant.full_name,
        "applying_for": applicant.applying_for,
        "expires_on": applicant.expires_at,
        "enquiry_url": public_enquiry_url(school),
        "office_email": school.contact_email,
        "office_phone": school.contact_phone,
        "requirements": applicant.requirement_profile,
    }


def _send(applicant: Applicant, subject: str, template: str, extra: dict | None = None) -> bool:
    """Render ``template``.txt and ``template``.html and send both halves.

    Multipart for the same reason the password-reset mail is: a client that
    refuses HTML still gets a complete message rather than a blank one.
    """
    if not applicant.parent_email:
        return False

    context = _context(applicant)
    context.update(extra or {})

    text_body = render_to_string(f"admissions/email/{template}.txt", context)
    html_body = render_to_string(f"admissions/email/{template}.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=_from_header(applicant.school),
        to=[applicant.parent_email],
        # The parent's reply has to reach the school, not our no-reply box.
        reply_to=[applicant.school.contact_email] if applicant.school.contact_email else None,
    )
    message.attach_alternative(html_body, "text/html")

    sent = message.send(fail_silently=True)
    if not sent:
        logger.warning(
            "admissions: could not send %s to %s for %s",
            template,
            applicant.parent_email,
            applicant.reference,
        )
    return bool(sent)


def send_enquiry_acknowledgement(applicant: Applicant) -> bool:
    """"We have your enquiry" -- sent the moment the public form is submitted.

    Carries the reference and the closing date, because those are the two
    things the parent will need if they ring the school in a fortnight.
    """
    return _send(
        applicant,
        subject=f"We have received your enquiry - {applicant.school.name}",
        template="enquiry_acknowledgement",
    )


def send_enquiry_reminder(applicant: Applicant) -> bool:
    """The nudge inside the window: continue before the enquiry lapses.

    Deliberately points the family back at the school -- its phone number, its
    office email, its own enquiry page -- rather than at a login. The
    application stage is completed with a member of staff, who takes the
    documents and the fee, so a "click here to continue" link that led to a
    form the parent cannot finish alone would be a worse experience than a
    phone number.

    Stamps ``reminder_sent_at`` only on success, so a send that failed is
    retried by the next run rather than silently skipped forever.
    """
    if applicant.status != ApplicantStatus.ENQUIRY:
        return False

    sent = _send(
        applicant,
        subject=f"Continue {applicant.first_name}'s application - {applicant.school.name}",
        template="enquiry_reminder",
        extra={"days_left": applicant.days_left},
    )
    if sent:
        applicant.reminder_sent_at = timezone.now()
        applicant.save(update_fields=["reminder_sent_at", "updated_at"])
    return sent


def send_decision(applicant: Applicant) -> bool:
    """The outcome. Two templates, because the two letters are not one letter
    with a different adjective.

    An offer tells the family what to do next; a rejection is short, says the
    decision is final for this intake, and does not pretend to be an offer that
    went wrong. Stamps ``decision_sent_at`` so the detail screen can show
    whether the parent has actually been told.
    """
    if applicant.status not in (ApplicantStatus.OFFERED, ApplicantStatus.REJECTED):
        return False

    offered = applicant.status == ApplicantStatus.OFFERED
    subject = (
        f"An offer of a place for {applicant.first_name} - {applicant.school.name}"
        if offered
        else f"About {applicant.first_name}'s application - {applicant.school.name}"
    )
    sent = _send(
        applicant,
        subject=subject,
        template="decision_offer" if offered else "decision_rejected",
        extra={"schedule": applicant.fee_schedule},
    )
    if sent:
        applicant.decision_sent_at = timezone.now()
        applicant.save(update_fields=["decision_sent_at", "updated_at"])
    return sent
