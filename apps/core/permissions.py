"""What each role is allowed to *do*, as distinct from what it can *see*.

Tenant scoping (``apps.core.tenancy``) answers "which rows?".  Capabilities
answer "which actions?".  Keeping them apart means a bursar can be given full
visibility of the academic and fee setup while still being unable to change it.

Capabilities are granted by permission role only -- never by job title.
"""

from __future__ import annotations

from enum import Enum

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from .roles import Role


class Capability(str, Enum):
    """A single thing an account may be permitted to do."""

    VIEW_ACADEMICS = "view_academics"
    MANAGE_ACADEMICS = "manage_academics"
    VIEW_FEES = "view_fees"
    MANAGE_FEES = "manage_fees"
    VIEW_STUDENTS = "view_students"
    MANAGE_STUDENTS = "manage_students"
    VIEW_MESSAGES = "view_messages"
    SEND_MESSAGES = "send_messages"
    VIEW_PAYMENTS = "view_payments"
    RECORD_PAYMENTS = "record_payments"
    VOID_PAYMENTS = "void_payments"
    VIEW_MESSAGING_IDENTITY = "view_messaging_identity"
    MANAGE_MESSAGING_IDENTITY = "manage_messaging_identity"
    MANAGE_SCHOOL_PROFILE = "manage_school_profile"
    # The school's logo, separately from the rest of its profile. A principal
    # corrects a misspelled school name or a wrong office number; what the
    # school *looks like* to parents on a receipt is the proprietor's call, and
    # they were explicit about that. Held by the school owner and the platform,
    # never by a principal.
    MANAGE_SCHOOL_LOGO = "manage_school_logo"
    # The school's campuses and the people who work at it. Separate from
    # MANAGE_SCHOOL_PROFILE because opening a second campus, or changing who
    # may sign in, is the proprietor's decision rather than the principal's.
    VIEW_BRANCHES = "view_branches"
    MANAGE_BRANCHES = "manage_branches"
    VIEW_STAFF = "view_staff"
    MANAGE_STAFF = "manage_staff"
    # What each level must bring to be admitted. The proprietor's policy, not
    # the principal's: a principal runs the pipeline under the rules, they do
    # not rewrite the rules. Everyone who can see admissions reads them.
    MANAGE_ADMISSION_REQUIREMENTS = "manage_admission_requirements"
    # Admissions. Four capabilities rather than the usual two, because the
    # client drew two lines here that the view/manage pair cannot express:
    # deciding on an application is narrower than working the pipeline, and
    # taking admission money is available to a bursar who cannot see the
    # pipeline at all.
    VIEW_ADMISSIONS = "view_admissions"
    MANAGE_ADMISSIONS = "manage_admissions"
    DECIDE_ADMISSIONS = "decide_admissions"
    VIEW_ADMISSION_PAYMENTS = "view_admission_payments"
    RECORD_ADMISSION_PAYMENTS = "record_admission_payments"
    # Which features a school has. The platform's decision and nobody else's:
    # a proprietor who could switch on their own paid add-ons would be writing
    # their own invoice. Granted only in _PLATFORM below, and it is the one
    # capability deliberately absent from a school owner's set.
    MANAGE_SCHOOL_MODULES = "manage_school_modules"
    # Switching an account off without deleting it. The platform's, for the same
    # reason the modules are: it is how a school's access is suspended, and a
    # proprietor who could do it could also lock out the platform's own support
    # account at their school.
    DEACTIVATE_ACCOUNTS = "deactivate_accounts"


#: Owner- and principal-level roles: set up the school, who attends it, and what
#: it charges.
_LEADERSHIP = frozenset(
    {
        Capability.VIEW_ACADEMICS,
        Capability.MANAGE_ACADEMICS,
        Capability.VIEW_FEES,
        Capability.MANAGE_FEES,
        Capability.VIEW_STUDENTS,
        Capability.MANAGE_STUDENTS,
        Capability.VIEW_MESSAGES,
        Capability.SEND_MESSAGES,
        Capability.VIEW_PAYMENTS,
        Capability.RECORD_PAYMENTS,
        Capability.VOID_PAYMENTS,
        Capability.VIEW_MESSAGING_IDENTITY,
        # What the school is called and what its logo is: the proprietor's
        # decision, and the principal's to correct. Not the bursar's -- they
        # take money, they do not decide how the school presents itself.
        Capability.MANAGE_SCHOOL_PROFILE,
        # Both read their school's campuses and staff list. Changing either is
        # granted separately, in _SCHOOL_ADMIN below.
        Capability.VIEW_BRANCHES,
        Capability.VIEW_STAFF,
        # Admissions: the principal and the owner run the pipeline and decide
        # who is offered a place. The client was explicit that the decision is
        # theirs and nobody else's.
        Capability.VIEW_ADMISSIONS,
        Capability.MANAGE_ADMISSIONS,
        Capability.DECIDE_ADMISSIONS,
        Capability.VIEW_ADMISSION_PAYMENTS,
        Capability.RECORD_ADMISSION_PAYMENTS,
    }
)

#: Opening a campus and changing who may sign in. The proprietor's decisions,
#: and the platform's on their behalf -- not the principal's, who runs one
#: campus rather than deciding how many there are.
#:
#: The logo sits here rather than in _LEADERSHIP for the same reason: a principal
#: may correct the school's name or its office number, and does not choose the
#: mark that goes on every receipt a parent is handed.
_SCHOOL_ADMIN = frozenset(
    {
        Capability.MANAGE_BRANCHES,
        Capability.MANAGE_STAFF,
        Capability.MANAGE_ADMISSION_REQUIREMENTS,
        Capability.MANAGE_SCHOOL_LOGO,
    }
)

#: The platform owner holds everything a school owner does, plus the actions
#: only the party holding the gateway account can take. Registering and
#: approving a school's Sender ID is one: the platform submits it to the
#: gateway, so a school approving its own would be marking its own homework and
#: the first message would be rejected anyway.
#:
#: Switching a school's modules is the other, and for the commercial reason
#: rather than a technical one: which features a school has is what the school
#: is paying for. A proprietor who could turn on the admissions add-on would be
#: deciding their own bill.
_PLATFORM = (
    _LEADERSHIP
    | _SCHOOL_ADMIN
    | {
        Capability.MANAGE_MESSAGING_IDENTITY,
        Capability.MANAGE_SCHOOL_MODULES,
        Capability.DEACTIVATE_ACCOUNTS,
    }
)

#: A bursar collects against the fee structure but does not decide it, and
#: records payments against the roster without owning it -- they need to find a
#: student to take money from them, not to enrol or remove one. Read-only on
#: the setup screens; the money-movement capabilities land with the payments
#: layer.
#:
#: Messaging and payments are the exceptions, and the reason is the same:
#: taking money and chasing it is the bursar's actual job. They record and
#: confirm payments and send parent reminders at the same level as a principal.
#: Neither edits school setup -- both only read the roster and the pricing the
#: bursar can already see.
#:
#: Voiding is the one money action they do not hold. Reversing confirmed money
#: is a supervisory act, so it sits with the principal and the owner. If the
#: school would rather the bursar could undo their own mistakes, add
#: VOID_PAYMENTS here -- the screens follow the capability table.
_BURSAR = frozenset(
    {
        Capability.VIEW_ACADEMICS,
        Capability.VIEW_FEES,
        Capability.VIEW_STUDENTS,
        Capability.VIEW_MESSAGES,
        Capability.SEND_MESSAGES,
        Capability.VIEW_PAYMENTS,
        Capability.RECORD_PAYMENTS,
        # Admission money is still money, and taking it is still the bursar's
        # job -- so these two are granted flatly, exactly as the termly pair
        # above are. What is deliberately absent is VIEW_ADMISSIONS: by default
        # a bursar sees the intake payments without seeing the pipeline. A
        # school whose bursar also runs the front desk turns that on per school
        # (AdmissionsConfig.bursar_can_view_applicants), which is why it cannot
        # be a fixed entry in this table -- see apps/admissions/access.py.
        #
        # DECIDE_ADMISSIONS is not grantable to a bursar at all. Who is offered
        # a place is the principal's call, and the client asked for that line
        # to hold regardless of how the rest is configured.
        Capability.VIEW_ADMISSION_PAYMENTS,
        Capability.RECORD_ADMISSION_PAYMENTS,
    }
)

_EVERYTHING = frozenset(Capability)

#: The grant table. Add a capability here, not with an ad-hoc check in a view.
ROLE_CAPABILITIES: dict[str, frozenset[Capability]] = {
    Role.PLATFORM_OWNER: _PLATFORM,
    Role.SCHOOL_OWNER: _LEADERSHIP | _SCHOOL_ADMIN,
    Role.PRINCIPAL: _LEADERSHIP,
    Role.BURSAR: _BURSAR,
}


def capabilities_for(role: str | None) -> frozenset[Capability]:
    """Capabilities granted to ``role``. Unknown roles get nothing."""
    if not role:
        return frozenset()
    return ROLE_CAPABILITIES.get(role, frozenset())


def capabilities_of(user) -> frozenset[Capability]:
    """What this account may actually do, here, today.

    The role table is the ceiling; a module its school does not have takes
    capabilities back off it. That second step is what closes the doors into a
    switched-off module that are drawn on *other* modules' screens -- the
    fee-reminder button on the bursar's dashboard is a messaging action sitting on
    a payments page, and the middleware that guards ``/messaging/`` cannot reach
    it.

    Withdrawing rather than never granting, deliberately: a capability is a fact
    about a role and stays one. ``capabilities_for(role)`` is still the pure
    answer, and is what the capability table's own tests assert on.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return frozenset()
    if getattr(user, "is_superuser", False):
        return _EVERYTHING

    granted = capabilities_for(getattr(user, "role", None))
    return granted - _withheld_from(user)


def _withheld_from(user) -> frozenset[Capability]:
    """Capabilities belonging to modules this account's school does not have.

    Imported inside the function: ``apps.core.modules`` reaches the schools app
    for the stored switches, and schools is built on ``apps.core.models``. A
    module-level import here would close that circle.
    """
    from .modules import enabled_for_user, withheld_capabilities

    withheld = withheld_capabilities(enabled_for_user(user))
    if not withheld:
        return frozenset()
    return frozenset(c for c in Capability if c.value in withheld)


def has_capability(user, capability: Capability) -> bool:
    return capability in capabilities_of(user)


class CapabilityRequiredMixin(LoginRequiredMixin):
    """Refuse the view unless the account holds :attr:`capability`.

    Signed-out visitors fall through to ``LoginRequiredMixin`` and are
    redirected to the login page; signed-in accounts without the capability get
    a 403, because bouncing them to a login form they have already passed would
    be a lie about what went wrong.
    """

    capability: Capability | None = None

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.capability is not None:
            if not has_capability(request.user, self.capability):
                raise PermissionDenied(
                    f"Your role does not allow {self.capability.value.replace('_', ' ')}."
                )
        return super().dispatch(request, *args, **kwargs)
