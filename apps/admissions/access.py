"""Who may see the pipeline, and who may decide on it.

Every other app in the platform answers "may this account do X?" from the
static table in :mod:`apps.core.permissions`, and admissions mostly does too.
One question it cannot answer that way: *whether a bursar sees applicants* is
the school's decision, not the platform's. Some schools run the front desk out
of the bursary and want them in the pipeline; most do not.

So there are two layers here, and the order matters:

* **The role table is the ceiling.** A bursar can never hold
  ``DECIDE_ADMISSIONS``, whatever any school switches on. Who is offered a
  place is the principal's call, and a per-school toggle that could move it
  would not be a configuration option, it would be a hole.
* **The school's config only ever widens *viewing*.** Turning
  ``bursar_can_view_applicants`` on lets a bursar read the board and the
  applicants on it. It grants nothing else -- not advancing a stage, not
  recording an assessment, and certainly not deciding.

Views use the mixins at the bottom rather than asking these questions by hand,
so the sidebar, the buttons on a page and the response to a typed-in URL can
never disagree about what an account is allowed to do.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from apps.core.permissions import Capability, has_capability
from apps.core.roles import Role

from .models import AdmissionsConfig


def config_for(user) -> AdmissionsConfig | None:
    """This account's school policy, fetched at most once per request.

    Two economies, both of which showed up as extra queries on every page a
    bursar loaded:

    * ``user.school_id``, never ``user.school`` -- the latter fetches the whole
      School row to read a primary key the user already carries;
    * memoised on the user instance, which lives exactly as long as the request,
      so the sidebar and half a dozen template checks share one lookup.

    Returns ``None`` for an account with no school (platform staff), which the
    callers read as "no school policy applies".
    """
    if getattr(user, "school_id", None) is None:
        return None
    cached = getattr(user, "_admissions_config", None)
    if cached is None:
        cached = AdmissionsConfig.for_school(user.school_id)
        user._admissions_config = cached
    return cached


def can_view_applicants(user) -> bool:
    """May this account see the pipeline and the applicants on it?

    Leadership always -- and answered from the static table, so they never pay
    for the lookup below. A bursar only where their school has said so.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if has_capability(user, Capability.VIEW_ADMISSIONS):
        return True
    if getattr(user, "role", None) != Role.BURSAR:
        return False
    config = config_for(user)
    return config is not None and config.bursar_can_view_applicants


def can_manage_applicants(user) -> bool:
    """May this account move an applicant along -- advance, assess, enrol?

    Never widened by the school toggle: reading a pipeline and running one are
    different jobs, and the toggle was asked for to answer the first.
    """
    return has_capability(user, Capability.MANAGE_ADMISSIONS)


def can_decide(user) -> bool:
    """May this account offer or refuse a place? Principal and owner only."""
    return has_capability(user, Capability.DECIDE_ADMISSIONS)


def can_view_admission_payments(user) -> bool:
    return has_capability(user, Capability.VIEW_ADMISSION_PAYMENTS)


def can_record_admission_payments(user) -> bool:
    return has_capability(user, Capability.RECORD_ADMISSION_PAYMENTS)


def admissions_capabilities(user) -> set[str]:
    """The strings templates test against, resolved once per request.

    Handed to the context by :func:`apps.admissions.context.admissions`, so a
    template writes ``{% if "view_admissions" in capabilities %}`` for the
    configurable ones exactly as it does for the fixed ones -- the difference
    between a table grant and a school's toggle stops mattering at the markup.
    """
    granted: set[str] = set()
    if can_view_applicants(user):
        granted.add(Capability.VIEW_ADMISSIONS.value)
    if can_manage_applicants(user):
        granted.add(Capability.MANAGE_ADMISSIONS.value)
    if can_decide(user):
        granted.add(Capability.DECIDE_ADMISSIONS.value)
    if can_view_admission_payments(user):
        granted.add(Capability.VIEW_ADMISSION_PAYMENTS.value)
    if can_record_admission_payments(user):
        granted.add(Capability.RECORD_ADMISSION_PAYMENTS.value)
    return granted


class _GateMixin(LoginRequiredMixin):
    """Signed-out visitors get the login page, signed-in ones get a 403.

    The same trade ``CapabilityRequiredMixin`` makes: bouncing an account that
    has already signed in back to a login form would be a lie about what went
    wrong.
    """

    denied_message = "Your role does not allow that."

    def test(self, user) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not self.test(request.user):
            raise PermissionDenied(self.denied_message)
        return super().dispatch(request, *args, **kwargs)


class ViewAdmissionsMixin(_GateMixin):
    """Read the pipeline. Leadership, plus a bursar where the school allows."""

    denied_message = (
        "Your role does not allow viewing applicants. A school owner or "
        "principal can turn this on for bursars in admissions settings."
    )

    def test(self, user) -> bool:
        return can_view_applicants(user)


class ManageAdmissionsMixin(_GateMixin):
    """Move applicants through the pipeline. Owner and principal only."""

    denied_message = "Your role does not allow managing applications."

    def test(self, user) -> bool:
        return can_manage_applicants(user)


class DecideAdmissionsMixin(_GateMixin):
    """Offer or refuse a place. Owner and principal only, always."""

    denied_message = "Only a school owner or principal can decide on an application."

    def test(self, user) -> bool:
        return can_decide(user)


class ViewAdmissionPaymentsMixin(_GateMixin):
    """Read the intake fee sheets and what has been collected against them.

    Every role reaches this, including a bursar whose school has not let them
    near the pipeline -- seeing admission money without seeing applicants is
    exactly the default the client asked for.
    """

    denied_message = "Your role does not allow viewing admission fees."

    def test(self, user) -> bool:
        return can_view_admission_payments(user)


class RecordAdmissionPaymentsMixin(_GateMixin):
    """Take admission money. The bursar's own screen."""

    denied_message = "Your role does not allow recording admission payments."

    def test(self, user) -> bool:
        return can_record_admission_payments(user)
