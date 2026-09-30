"""Mail the platform sends as itself.

One function so far -- the welcome a school gets when it signs itself up. It lives
here rather than in ``views.py`` because sending is not a view's job and because a
management command or a later signal should be able to send the same message
without going through an HTTP request.

Two rules this file follows, both borrowed from
``apps.admissions.notifications`` where they were worked out:

* **Best effort.** A welcome that fails to send must never turn a completed signup
  into an error page. The school exists, the owner is signed in, and the mail is
  the least important thing that happened in that request -- so a failure is
  logged with its traceback and swallowed.
* **Absolute links.** An email has no request to resolve a relative path against,
  so every URL is built from ``PUBLIC_BASE_URL``. Where that is unset the message
  is not sent at all rather than sent with links that go nowhere, and the log says
  which it was.

Unlike the admissions mail, this one is *ours*: it is signed SCHOOLCORD, uses the
platform's own shell at ``templates/email/_base.html``, and goes out under
``DEFAULT_FROM_EMAIL`` with no display-name substitution.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from .branding import branding

logger = logging.getLogger(__name__)


def _site() -> tuple[str, str] | None:
    """``(origin, host)`` from ``PUBLIC_BASE_URL``, or ``None`` if unusable."""
    url = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return url, parts.netloc


#: The four setup steps, as the welcome email lists them.
#:
#: Written here rather than read from ``views.onboarding_steps``, and the reason is
#: worth stating: that function counts rows to decide what is *done*, which needs a
#: tenant context and a database. This message goes to a school that was created
#: three lines ago and has none of it yet, so what it needs is the ladder itself,
#: not a progress report. The labels are kept in step by hand, and a test asserts
#: the two lists still describe the same four steps.
WELCOME_STEPS: tuple[tuple[str, str], ...] = (
    (
        "Academic setup",
        "The classes you run, and the subjects taught in them.",
    ),
    (
        "Finance setup",
        "The current term, and what each class owes in it.",
    ),
    (
        "Import students",
        "Bring your roster over from a spreadsheet, or add students one at a time.",
    ),
    (
        "Invite staff",
        "Principals and bursars, each with their own access.",
    ),
)


def send_welcome_email(school, branch, owner) -> bool:
    """Welcome a new school and point it at the setup it has left.

    Returns ``True`` only if a message was handed to the mail backend. Never
    raises: see the module docstring.

    Explicitly *not* a verification step. The account works the moment signup
    finishes -- the owner is signed in before this is called -- and nothing here
    gates anything. A "confirm your email" message that confirmed nothing would
    teach people to expect one that does.
    """
    if not getattr(owner, "email", ""):
        # An owner with no address is not an error: the platform creates accounts
        # this way for schools it sets up by hand. There is simply nowhere to send.
        return False

    site = _site()
    if site is None:
        logger.warning(
            "Welcome email for %r not sent: PUBLIC_BASE_URL is not set, so the "
            "links in it would go nowhere.",
            school.name,
        )
        return False
    origin, host = site

    context = {
        **branding(),
        "school": school,
        "branch": branch,
        "owner": owner,
        "steps": [
            {"label": label, "description": description}
            for label, description in WELCOME_STEPS
        ],
        "site_url": origin,
        "onboarding_url": origin + reverse("core:onboarding"),
        # The shell builds the logo's absolute URL out of these, the same way the
        # password-reset mail does -- see templates/email/_base.html.
        "protocol": urlsplit(origin).scheme,
        "domain": host,
    }

    try:
        message = EmailMultiAlternatives(
            subject=f"Welcome to {context['product_name']}",
            body=render_to_string("email/welcome.txt", context),
            to=[owner.email],
        )
        message.attach_alternative(
            render_to_string("email/welcome.html", context), "text/html"
        )
        message.send()
    except Exception:
        # Every failure mode is the same failure mode from here: the school is
        # created, the owner is signed in, and the only thing that did not happen
        # is an informational email.
        logger.exception("Welcome email for %r could not be sent", school.name)
        return False

    return True
