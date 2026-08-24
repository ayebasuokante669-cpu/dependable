"""The shell's own screens: the front door, the way in, and where each role lands.

Three groups live here.

**Public** -- the landing page and signup are the only screens on the platform
that run with no tenant context at all. Signup is where a tenant comes into
existence, so it is the one place that may reach past the scoped managers; every
such call is spelled ``all_objects`` and explained where it happens.

**Role dashboards** -- four of them, because "the dashboard" is a different
question for each role. A platform owner wants schools, an owner wants branches,
a principal wants their campus, a bursar wants money. One screen answering all
four would be four screens with three of them hidden.

None of them filters by school or branch: the scoped managers have already done
it, so the platform overview and the branch dashboard below are running the same
``School.objects.count()`` and getting the right answer for each caller.

**Onboarding** -- the four setup steps a new school walks through, with each
step's completion derived from whether the data exists rather than stored on a
flag that can lie.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.db.models import Count, Q, Sum
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import FormView, TemplateView

from apps.academics.models import Class, Subject
from apps.fees.models import FeeComponent, FeeStructure, Term
from apps.schools.models import Branch, School
from apps.students.models import Student, StudentStatus

from .forms import SchoolSignupForm
from .navigation import home_url_for, home_url_name
from .roles import Role, scope_for

ZERO = Decimal("0")


# ===========================================================================
# Public
# ===========================================================================


class LandingView(TemplateView):
    """The front door: what this is, and the two ways in.

    Signed-in visitors never see it -- they are sent to their own dashboard, so
    the root URL is a useful destination whoever types it.
    """

    template_name = "core/landing.html"

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return HttpResponseRedirect(home_url_for(request.user))
        return super().get(request, *args, **kwargs)


class RoleAwareLoginView(LoginView):
    """Django's login, pointed at the right dashboard afterwards.

    ``next=`` still wins when present and safe -- that is what makes a
    deep link survive the login page -- and the role's own home is the fallback
    instead of one shared ``LOGIN_REDIRECT_URL`` for everybody.
    """

    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_default_redirect_url(self) -> str:
        return home_url_for(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Sign in"
        return context


class SignupView(FormView):
    """Onboarding step 1: create the school, the first branch and the owner."""

    template_name = "core/signup.html"
    form_class = SchoolSignupForm
    extra_context = {"page_title": "Create your school account"}

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return HttpResponseRedirect(home_url_for(request.user))
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        school, branch, owner = form.save()
        # Nothing authenticated this user -- they were created a line ago -- so
        # the backend has to be named explicitly.
        login(self.request, owner, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(
            self.request,
            f"{school.name} is set up, with {branch.name} as its first campus. "
            f"Four steps to go.",
        )
        return HttpResponseRedirect(reverse("core:onboarding"))


# ===========================================================================
# Where each role lands
# ===========================================================================


def dashboard(request):
    """``/dashboard/`` -- send whoever asked to their own dashboard.

    Kept as a real URL so ``LOGIN_REDIRECT_URL``, a bookmarked ``/dashboard/``
    and anything else that wants "the dashboard" without knowing the role has
    somewhere to point.
    """
    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={request.path}")
    return HttpResponseRedirect(home_url_for(request.user))


class RoleDashboardMixin(LoginRequiredMixin):
    """A dashboard that belongs to one role.

    Reaching another role's dashboard by typing its URL is not a data leak --
    the scoped managers see to that -- but it is a confusing screen answering
    somebody else's question, so the caller is sent to their own instead.
    """

    #: The URL name of this dashboard, matched against the role's own home.
    home_url_name: str = ""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            role = getattr(request, "tenant", None)
            role = role.role if role is not None else request.user.role
            if home_url_name(role) != self.home_url_name:
                return HttpResponseRedirect(home_url_for(request.user))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = getattr(self.request, "tenant", None)
        role = role.role if role is not None else self.request.user.role
        context["role_label"] = Role(role).label if role in Role.values else "No role"
        context["scope"] = scope_for(role).value
        return context


class PlatformOverviewView(RoleDashboardMixin, TemplateView):
    """Every school on the platform, and how much of it is in use."""

    template_name = "core/dashboard_platform.html"
    home_url_name = "core:platform_overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        User = get_user_model()

        # Platform scope means these managers filter nothing -- the counts are
        # the whole platform, which is the question this screen asks.
        context["school_count"] = School.objects.count()
        context["branch_count"] = Branch.objects.count()
        context["user_count"] = User.objects.count()
        context["student_count"] = Student.objects.count()

        context["schools"] = (
            School.objects.annotate(
                branches_count=Count("branches", distinct=True),
                students_count=Count(
                    "students_student_set",
                    filter=Q(students_student_set__status=StudentStatus.ACTIVE),
                    distinct=True,
                ),
            ).order_by("name")[:25]
        )
        context["page_title"] = "Platform overview"
        return context


class SchoolDashboardView(RoleDashboardMixin, TemplateView):
    """A school owner's view: every campus they run, side by side."""

    template_name = "core/dashboard_school.html"
    home_url_name = "core:school_dashboard"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        branches = list(
            Branch.objects.annotate(
                students_count=Count(
                    "students",
                    filter=Q(students__status=StudentStatus.ACTIVE),
                    distinct=True,
                ),
                classes_count=Count(
                    "classes", filter=Q(classes__is_active=True), distinct=True
                ),
            ).order_by("name")
        )
        # One query for every branch's current term rather than one per branch.
        current_terms = {
            term.branch_id: term
            for term in Term.objects.filter(is_current=True)
        }
        context["rows"] = [
            {"branch": branch, "term": current_terms.get(branch.pk)}
            for branch in branches
        ]

        context["branch_count"] = len(branches)
        context["student_count"] = sum(b.students_count for b in branches)
        context["class_count"] = sum(b.classes_count for b in branches)
        context["branches_without_a_term"] = [
            b.name for b in branches if b.pk not in current_terms
        ]
        context["setup"] = setup_progress(self.request)
        context["page_title"] = "School overview"
        return context


class BranchDashboardView(RoleDashboardMixin, TemplateView):
    """A principal's campus: who is enrolled, in what, and what is unpriced."""

    template_name = "core/dashboard_branch.html"
    home_url_name = "core:branch_dashboard"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        term = Term.objects.filter(is_current=True).first()

        context["term"] = term
        context["student_count"] = Student.objects.filter(
            status=StudentStatus.ACTIVE
        ).count()
        context["class_count"] = Class.objects.filter(is_active=True).count()
        context["subject_count"] = Subject.objects.filter(is_active=True).count()

        classes = list(
            Class.objects.filter(is_active=True).annotate(
                students_count=Count(
                    "students",
                    filter=Q(students__status=StudentStatus.ACTIVE),
                    distinct=True,
                ),
            )
        )
        priced = set()
        if term is not None:
            priced = set(
                FeeStructure.objects.filter(term=term).values_list(
                    "school_class_id", flat=True
                )
            )
        context["classes"] = [
            {"klass": klass, "is_priced": klass.pk in priced} for klass in classes
        ]
        # The setup gap a principal needs to see: children enrolled in a class
        # nobody has priced for the term they are being billed for.
        context["unpriced_classes"] = [
            row for row in context["classes"]
            if not row["is_priced"] and row["klass"].students_count
        ]
        context["setup"] = setup_progress(self.request)
        context["page_title"] = "Branch dashboard"
        return context


class BursarDashboardView(RoleDashboardMixin, TemplateView):
    """A bursar's view: what the term is worth, and what is not yet priced.

    Read-only throughout, which matches the capability table -- a bursar sees
    every figure here and cannot change any of them until the payments layer
    gives them something to record.
    """

    template_name = "core/dashboard_bursar.html"
    home_url_name = "core:bursar_dashboard"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        term = Term.objects.filter(is_current=True).first()
        context["term"] = term

        rows: list[dict] = []
        expected_total = ZERO
        if term is not None:
            structures = list(
                FeeStructure.objects.filter(term=term).select_related("school_class")
            )
            # Two queries for the whole table however many classes there are:
            # one for the line-item totals, one for the head counts.
            totals = {
                row["fee_structure__school_class_id"]: row["amount"] or ZERO
                for row in FeeComponent.objects.filter(
                    fee_structure__in=structures
                )
                .values("fee_structure__school_class_id")
                .annotate(amount=Sum("amount"))
            }
            head_counts = {
                row["school_class_id"]: row["n"]
                for row in Student.objects.filter(status=StudentStatus.ACTIVE)
                .values("school_class_id")
                .annotate(n=Count("id"))
            }
            for structure in structures:
                per_student = totals.get(structure.school_class_id, ZERO)
                students = head_counts.get(structure.school_class_id, 0)
                line = per_student * students
                expected_total += line
                rows.append({
                    "klass": structure.school_class,
                    "per_student": per_student,
                    "students": students,
                    "expected": line,
                })

            context["unpriced_classes"] = (
                Class.objects.filter(is_active=True)
                .exclude(pk__in=[s.school_class_id for s in structures])
                .annotate(
                    students_count=Count(
                        "students",
                        filter=Q(students__status=StudentStatus.ACTIVE),
                        distinct=True,
                    )
                )
                .filter(students_count__gt=0)
            )

        context["rows"] = rows
        context["expected_total"] = expected_total
        context["student_count"] = Student.objects.filter(
            status=StudentStatus.ACTIVE
        ).count()
        context.update(
            with_outstanding(collection_summary(term), expected_total)
        )
        context["page_title"] = "Finance dashboard"
        return context


def collection_summary(term) -> dict:
    """What has actually come in against ``term``, and what is still waiting.

    Resolved through the app registry rather than imported: the dashboards
    shipped before payments did, and this screen must render whether or not
    that app is installed. Confirmed money only -- pending receipts are
    reported separately, because a bursar needs to know the difference between
    money counted and money merely handed in.
    """
    from django.apps import apps as django_apps

    summary = {"collected_total": ZERO, "pending_total": ZERO, "pending_count": 0}
    if term is None or not django_apps.is_installed("apps.payments"):
        return summary

    from apps.payments.models import Payment, PaymentStatus

    totals = Payment.objects.filter(term=term).aggregate(
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


# ===========================================================================
# Onboarding
# ===========================================================================


def setup_progress(request) -> dict:
    """The onboarding checklist plus the summary every caller ends up needing.

    Returned as one object so a dashboard can ask "is there anything left?"
    without counting steps in the template.
    """
    steps = onboarding_steps(request)
    done = [step for step in steps if step["done"]]
    return {
        "steps": steps,
        "total": len(steps),
        "done_count": len(done),
        "is_complete": len(done) == len(steps),
        "next_step": next((s for s in steps if not s["done"]), None),
        "url": reverse("core:onboarding"),
    }


def onboarding_steps(request) -> list[dict]:
    """The four setup steps, each marked done or not from the data itself.

    Derived, never stored. A "setup complete" flag on the school would be a
    second source of truth that goes stale the moment someone deletes the last
    class, and a school would be told it had finished a step it had not.

    Every count goes through a scoped manager, so an owner sees their own
    school's progress and a principal their own branch's.
    """
    User = get_user_model()
    return [
        {
            "label": "Academic setup",
            "description": "The classes you run and the subjects taught in them.",
            "url": reverse("academics:class_list"),
            "done": Class.objects.exists(),
            "count": Class.objects.count(),
            "noun": "class",
        },
        {
            "label": "Finance setup",
            "description": "The current term, and what each class owes in it.",
            "url": reverse("fees:structure_list"),
            "done": FeeStructure.objects.exists() and Term.objects.exists(),
            "count": FeeStructure.objects.count(),
            "noun": "fee structure",
        },
        {
            "label": "Import students",
            "description": "Bring your roster over from a spreadsheet, or add "
                           "students one at a time.",
            "url": reverse("students:student_import"),
            "done": Student.objects.exists(),
            "count": Student.objects.count(),
            "noun": "student",
        },
        {
            "label": "Invite staff",
            "description": "Principals and bursars, each with their own access.",
            "url": reverse("admin:accounts_user_changelist"),
            # The owner themselves does not count as having invited anybody.
            "done": User.scoped.count() > 1,
            "count": max(User.scoped.count() - 1, 0),
            "noun": "colleague",
        },
    ]


class OnboardingView(LoginRequiredMixin, TemplateView):
    """Where signup lands, and where the shell links back to until it is done."""

    template_name = "core/onboarding.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["setup"] = setup_progress(self.request)
        context["page_title"] = "Set up your school"
        return context
