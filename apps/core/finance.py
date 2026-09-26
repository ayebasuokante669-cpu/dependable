"""Money and payment-state derivations, shared by the dashboards and Reports.

These lived in ``apps/core/views.py`` while the four dashboards were the only
screens that asked them. Reports asks the same questions of a wider slice --
several campuses at once rather than one -- so they moved here and grew a
plural: each takes a *set* of terms, and optionally the students to measure,
rather than exactly one of each. The dashboards pass a set of one and get the
same answers they got before.

Nothing here decides what anything means. ``apps.students.fees`` owns what a
student is expected to pay and which of the four states that puts them in;
``apps.payments.balances`` owns what counts as paid. Both are reached through
the app registry rather than imported, because the dashboards shipped before
payments did and every screen here has to render whether or not that app is
installed.
"""

from __future__ import annotations

from decimal import Decimal

from django.apps import apps as django_apps
from django.db.models import Count, Q, Sum

ZERO = Decimal("0")

#: The four payment states, in the one order every chart and legend reads in:
#: the ladder from settled to worst. Fixed, so a state keeps its colour and its
#: position even in a term where its count is zero. The keys are also the
#: design-system colour tokens -- ``--color-paid``, ``status-dot-overdue`` -- so
#: the stacked bar, the pill beside a child's name and the outstanding list
#: cannot drift into saying the same thing in different colours.
PAYMENT_STATES: tuple[tuple[str, str], ...] = (
    ("paid", "Paid"),
    ("partial", "Part paid"),
    ("unpaid", "Unpaid"),
    ("overdue", "Overdue"),
)


def empty_buckets() -> list[dict]:
    """The four states with nothing in them yet."""
    return [{"key": key, "label": label, "count": 0} for key, label in PAYMENT_STATES]


def _terms(terms) -> list:
    """Normalise "one term, no term, or several" into a list.

    Callers arrive from both directions: a dashboard has resolved exactly one
    current term (or none), while a report has one per campus. Accepting either
    is cheaper than making every caller wrap its own argument.
    """
    if terms is None:
        return []
    if hasattr(terms, "pk"):
        return [terms]
    return [term for term in terms if term is not None]


def collection_summary(terms) -> dict:
    """What has actually come in against ``terms``, and what is still waiting.

    Confirmed money only -- pending receipts are reported separately, because a
    bursar needs to know the difference between money counted and money merely
    handed in.

    This is the *till* question: every confirmed payment credited to those
    terms, whoever it came from. It is deliberately not the same figure as the
    sum of the current roster's balances, which is the *chase* question and is
    what :func:`status_breakdown` and the outstanding list answer. They differ
    whenever a student who has paid has since left, and the report says so
    rather than quietly picking one.
    """
    summary = {"collected_total": ZERO, "pending_total": ZERO, "pending_count": 0}
    wanted = _terms(terms)
    if not wanted or not django_apps.is_installed("apps.payments"):
        return summary

    from apps.payments.models import Payment, PaymentStatus

    totals = Payment.objects.filter(term__in=wanted).aggregate(
        collected=Sum("amount", filter=Q(status=PaymentStatus.CONFIRMED)),
        pending=Sum("amount", filter=Q(status=PaymentStatus.PENDING)),
        pending_count=Count("id", filter=Q(status=PaymentStatus.PENDING)),
    )
    summary["collected_total"] = totals["collected"] or ZERO
    summary["pending_total"] = totals["pending"] or ZERO
    summary["pending_count"] = totals["pending_count"] or 0
    return summary


def with_outstanding(summary: dict, expected_total) -> dict:
    """Add the derived outstanding figure. Never negative: overpayment across
    a branch is a credit sitting somewhere, not a negative debt."""
    summary["outstanding_total"] = max(
        expected_total - summary["collected_total"], ZERO
    )
    return summary


def status_breakdown(terms, students=None) -> dict:
    """How many students sit in each payment state.

    Counted, not stored, and counted from the same derivation every other
    screen uses -- ``apps.students.fees`` -- so the chart, the outstanding list
    and the pill beside a child's name can never disagree.

    ``terms`` is only a short-circuit -- "is any term current in the slice being
    asked about at all". It is deliberately not used to price anything:
    ``fees.load`` resolves each student's own campus term itself, which is what
    lets a proprietor looking at four campuses on different terms get one honest
    breakdown instead of three campuses measured against a fourth one's
    calendar.

    ``students`` narrows the roster being measured; left out, it is every active
    student the caller can see. A report on one campus passes that campus's
    students, and gets the same numbers the principal's own dashboard shows.
    """
    wanted = _terms(terms)
    if not wanted or not django_apps.is_installed("apps.payments"):
        return {"buckets": empty_buckets(), "total": 0}

    from apps.students import fees as student_fees
    from apps.students.models import Student, StudentStatus

    if students is None:
        students = Student.objects.filter(status=StudentStatus.ACTIVE).select_related(
            "school_class"
        )
    students = list(students)
    return tally_states(students, student_fees.load(students))


def tally_states(students, schedule) -> dict:
    """The four states across ``students``, given a schedule already loaded.

    Split out from :func:`status_breakdown` because Reports loads the schedule
    once and then asks this question of the whole roster, of each campus and of
    each class in turn. Reloading it per group would be the same figures worked
    out four times, and four chances for them to disagree.

    Returns the four states in :data:`PAYMENT_STATES` order with their counts,
    plus the total they are a share of.
    """
    buckets = empty_buckets()
    by_key = {bucket["key"]: bucket for bucket in buckets}
    for student in students:
        position = schedule.position_for(student)
        # A student in a class with no fee structure is not in any of the four
        # states -- they are unpriced, which is a setup gap rather than a
        # payment state. Counting them as "unpaid" would invent a debt.
        if not position.is_priced:
            continue
        bucket = by_key.get(position.state)
        if bucket is not None:
            bucket["count"] += 1
    return {
        "buckets": buckets,
        "total": sum(bucket["count"] for bucket in buckets),
    }
