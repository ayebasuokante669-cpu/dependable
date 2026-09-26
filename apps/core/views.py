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

from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView, PasswordResetView
from django.db.models import Count, Q, Sum
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import FormView, TemplateView, UpdateView

from apps.academics.models import Class, Subject
from apps.fees.models import FeeComponent, FeeStructure, Term
from apps.schools.models import Branch, School
from apps.students.models import Student, StudentStatus

from .branding import PRIVACY_EMAIL, branding
# The money and payment-state derivations the dashboards and Reports share.
# Re-exported by name so `from apps.core.views import status_breakdown` -- which
# is how the dashboard tests reach it -- keeps working after the move.
from .finance import collection_summary, status_breakdown, with_outstanding
from .forms import SchoolProfileForm, SchoolSignupForm
from .navigation import home_url_for, home_url_name
from .permissions import Capability, CapabilityRequiredMixin
from .roles import Role, scope_for
from .seo import structured_data

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

    #: Static path to the hero's background photograph, relative to a
    #: STATICFILES_DIRS root.
    #:
    #: Set to ``None`` and the template omits the <img> entirely rather than
    #: pointing at a placeholder: the brand gradient underneath is a finished
    #: hero on its own, so a missing asset costs texture rather than leaving a
    #: broken image in the most prominent position on the site.
    #:
    #: Whatever goes here is treated heavily by `.hero-photo` -- thrown to
    #: greyscale, re-tinted to the brand blue, darkened and blurred. That
    #: treatment is atmosphere, **not** a privacy mechanism: a CSS filter is a
    #: rendering choice and can be turned off. So the rule is about the file
    #: itself -- an empty room, desks or a chalkboard, and never an
    #: identifiable child.
    HERO_IMAGE: str | None = "img/hero-classroom.jpg"

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return HttpResponseRedirect(home_url_for(request.user))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["structured_data"] = structured_data(self.request)
        context["hero_image"] = self.HERO_IMAGE
        return context


class PrivacyView(TemplateView):
    """The privacy policy. Public, and readable signed in as well as out.

    The date is a constant rather than ``now``: "last updated" has to mean the
    day the words changed, and a template that printed today's date would claim
    a review that never happened.
    """

    template_name = "core/privacy.html"
    #: Bump when the policy text changes.
    POLICY_UPDATED = date(2026, 9, 18)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["policy_updated"] = self.POLICY_UPDATED
        context["privacy_email"] = PRIVACY_EMAIL
        context["page_title"] = "Privacy"
        return context


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
        # Both panels are rendered so switching between them costs no round
        # trip -- see registration/_auth_switch.html. The counterpart form is
        # unbound: nothing was submitted to it.
        context["auth_panel"] = "login"
        context["login_form"] = context["form"]
        context["signup_form"] = SchoolSignupForm()
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        # Greeting the person by name is the confirmation: it says the sign-in
        # worked *and* which account it worked as, which matters at a school
        # where one office computer is shared.
        who = self.request.user.get_full_name() or self.request.user.username
        messages.success(self.request, f"Signed in as {who}.")
        return response

    def form_invalid(self, form):
        # The form already prints why inline, next to the fields. The toast is
        # for the case that costs people the most time: pressing Sign in,
        # looking away, and looking back at a page that seems unchanged.
        messages.error(
            self.request, "Sign-in failed. Check the email and password."
        )
        return super().form_invalid(form)


class BrandedLogoutView(LogoutView):
    """Django's logout, with something to show for it.

    Signing out lands on the public landing page, which looks exactly like the
    landing page of somebody who was never signed in -- so without this there
    is no confirmation that the button did anything.

    The message is added *after* the parent runs. ``logout()`` flushes the
    session, and a message written before that would be flushed with it.
    """

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        messages.success(request, "Signed out. See you soon.")
        return response


def permission_denied(request, exception=None, template_name="403.html"):
    """Django's ``handler403``, with the refusal said out loud.

    The refusal itself is unchanged: the view never ran, the status is still
    403, and every test that asserts a bursar cannot reach an academic-setup
    URL still asserts exactly that. What this adds is a toast carrying the
    reason, so the answer arrives in the same place every other answer in the
    app does rather than only as a page the reader has to stop and parse.

    The message comes from the exception when the capability check wrote one
    (``CapabilityRequiredMixin`` names the capability), and falls back to plain
    language when something else raised it.

    Note what this deliberately is *not*: a prompt for credentials. A signed-in
    principal who lacks a capability does not need to authenticate again --
    they are already who they are -- so asking would be theatre. They are told,
    and they carry on.
    """
    from django.template import loader

    detail = str(exception or "").strip()
    messages.error(
        request,
        detail or "You are not authorised to perform this action.",
    )
    return HttpResponseForbidden(
        loader.render_to_string(template_name, {"exception": detail}, request)
    )


class BrandedPasswordResetView(PasswordResetView):
    """Django's password reset, with the product name reaching the email.

    The reset email and its subject are rendered by the *form*, not by a view,
    and with a plain context rather than a request -- so context processors do
    not run and ``{{ product_name }}`` in those two templates would silently
    render as nothing. ``extra_email_context`` is Django's supported way to
    hand them a value, and it keeps the name in one file rather than hardcoded
    into the mail.

    Silent is the operative word: an empty product name in an email is not an
    error anywhere, it just quietly ships "Reset your  password" to a customer.

    The HTML half is sent multipart alongside the plain-text body Django has
    always sent, so a client that refuses HTML -- or a reader who prefers it --
    still gets a complete message rather than a blank one.
    """

    extra_email_context = branding()
    html_email_template_name = "registration/password_reset_email_html.html"


class SignupView(FormView):
    """Onboarding step 1: create the school, the first branch and the owner."""

    template_name = "core/signup.html"
    form_class = SchoolSignupForm
    extra_context = {"page_title": "Create your school account"}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["auth_panel"] = "signup"
        context["signup_form"] = context["form"]
        # Imported here rather than at module scope: core importing the auth
        # form at load time would have core and django.contrib.auth reaching
        # into each other before either app registry is ready.
        from django.contrib.auth.forms import AuthenticationForm

        context["login_form"] = AuthenticationForm()
        return context

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return HttpResponseRedirect(home_url_for(request.user))
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        school, branch, owner = form.save()
        # Nothing authenticated this user -- they were created a line ago -- so
        # the backend has to be named explicitly.
        login(self.request, owner, backend="apps.accounts.backends.EmailOrUsernameBackend")
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
        # Display-only. `any current term` is the guard; the breakdown itself
        # resolves each campus's own term -- see status_breakdown.
        context["status"] = status_breakdown(next(iter(current_terms.values()), None))
        context["busiest_campus"] = max(
            (b.students_count for b in branches), default=0
        )
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
        # Display-only, from the same derivation the bursar's screen uses.
        context["status"] = status_breakdown(term)
        context["busiest_class"] = max(
            (klass.students_count for klass in classes), default=0
        )
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
        # Display-only, and derived from the same source as everything else on
        # the page -- see status_breakdown.
        context["status"] = status_breakdown(term)
        context["busiest_expected"] = max(
            (row["expected"] for row in rows), default=ZERO
        )
        context["page_title"] = "Finance dashboard"
        return context


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

    The school's own profile -- its name and logo -- is deliberately *not* a
    step. Signup already sets the name, and a logo is optional, so any test of
    "done" would either tick itself the moment the account existed or could
    never be ticked at all. It is prompted from the school card at the top of
    the onboarding page and from Settings instead.
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


class SchoolSettingsView(CapabilityRequiredMixin, UpdateView):
    """The school's own profile: name, logo, office contact details.

    The home the "Settings" nav entry has been pointing at since the shell was
    built, and the place onboarding's last step sends a proprietor. Scoped to
    the caller's own school by ``get_object`` rather than by a URL parameter --
    there is exactly one school a school user can edit, and offering an id in
    the URL would only invite guessing at somebody else's.
    """

    form_class = SchoolProfileForm
    template_name = "core/school_settings.html"
    capability = Capability.MANAGE_SCHOOL_PROFILE
    success_url = reverse_lazy("core:school_settings")

    def get_object(self, queryset=None):
        from django.http import Http404

        school = getattr(self.request.user, "school", None)
        if school is None:
            # Platform staff have no school of their own to configure. They
            # edit any school through the admin, which is where cross-tenant
            # work belongs.
            raise Http404("This account is not attached to a school.")
        return school

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "School settings"
        context["setup"] = setup_progress(self.request)
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"{self.object.name} updated.")
        return response


class OnboardingView(LoginRequiredMixin, TemplateView):
    """Where signup lands, and where the shell links back to until it is done."""

    template_name = "core/onboarding.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["setup"] = setup_progress(self.request)
        context["page_title"] = "Set up your school"
        return context
