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

#: The platform owner holds everything a school owner does, plus the actions
#: only the party holding the gateway account can take. Registering and
#: approving a school's Sender ID is one: the platform submits it to the
#: gateway, so a school approving its own would be marking its own homework and
#: the first message would be rejected anyway.
_PLATFORM = _LEADERSHIP | {Capability.MANAGE_MESSAGING_IDENTITY}

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
    Role.SCHOOL_OWNER: _LEADERSHIP,
    Role.PRINCIPAL: _LEADERSHIP,
    Role.BURSAR: _BURSAR,
}


def capabilities_for(role: str | None) -> frozenset[Capability]:
    """Capabilities granted to ``role``. Unknown roles get nothing."""
    if not role:
        return frozenset()
    return ROLE_CAPABILITIES.get(role, frozenset())


def capabilities_of(user) -> frozenset[Capability]:
    if user is None or not getattr(user, "is_authenticated", False):
        return frozenset()
    if getattr(user, "is_superuser", False):
        return _EVERYTHING
    return capabilities_for(getattr(user, "role", None))


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
