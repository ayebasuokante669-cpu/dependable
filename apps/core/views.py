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
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import DetailView, FormView, TemplateView, UpdateView, View

from apps.academics.models import Class, Subject
from apps.fees.models import FeeComponent, FeeStructure, Term
from apps.schools.models import Branch, School, SchoolModule
from apps.students.models import Student, StudentStatus

from .branding import PRIVACY_EMAIL, branding
# The money and payment-state derivations the dashboards and Reports share.
# Re-exported by name so `from apps.core.views import status_breakdown` -- which
# is how the dashboard tests reach it -- keeps working after the move.
from .finance import collection_summary, status_breakdown, with_outstanding
from .forms import SchoolProfileForm, SchoolSignupForm
from .modules import BY_KEY, MODULES, state_for_school
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
    """Every school on the platform, as a card you open.

    The platform owner's job is not reading a table of counts; it is picking a
    school and doing something to it. So each school is a card with its own logo
    and name, and the card is the link -- into :class:`PlatformSchoolView`, where
    the plan, the campuses and the module switches are.

    Platform scope means the managers here filter nothing, which is the question
    this screen asks. It is also why every count below is a plain
    ``.count()`` and still right.
    """

    template_name = "core/dashboard_platform.html"
    home_url_name = "core:platform_overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        User = get_user_model()

        context["school_count"] = School.objects.count()
        context["branch_count"] = Branch.objects.count()
        context["user_count"] = User.objects.count()
        context["student_count"] = Student.objects.count()

        schools = list(
            School.objects.annotate(
                branches_count=Count("branches", distinct=True),
                students_count=Count(
                    "students_student_set",
                    filter=Q(students_student_set__status=StudentStatus.ACTIVE),
                    distinct=True,
                ),
                staff_count=Count("users", distinct=True),
            ).order_by("name")
        )
        # One query for every school's switches rather than one per card. The
        # cards show which add-ons each school has, because "who is paying for
        # admissions?" is a question this screen should answer at a glance.
        decided: dict[int, dict[str, bool]] = {}
        for school_id, key, enabled in SchoolModule.objects.values_list(
            "school_id", "key", "enabled"
        ):
            decided.setdefault(school_id, {})[key] = enabled
        context["cards"] = [
            {"school": school, "modules": _module_chips(decided.get(school.pk, {}))}
            for school in schools
        ]
        context["page_title"] = "Schools"
        return context


def _module_chips(decided: dict) -> list[dict]:
    """The toggleable modules and whether this school has them, for a card.

    Takes the school's decided rows rather than its id, so a list of thirty
    schools costs one query in total. The default comes from the registry, the
    same way :func:`apps.core.modules.enabled_for_school` reads it.
    """
    from .modules import TOGGLEABLE

    return [
        {
            "label": module.label,
            "key": module.key,
            "enabled": decided.get(module.key, module.default_on),
        }
        for module in TOGGLEABLE
    ]


class PlatformSchoolView(CapabilityRequiredMixin, DetailView):
    """One school, everything the platform knows about it, and its switches.

    Gated on ``MANAGE_SCHOOL_MODULES``, which only the platform owner holds -- so
    a proprietor typing this URL gets a 403 rather than a screen showing them
    somebody else's school, and the sidebar never offers it to them either.

    ``School.objects`` rather than ``all_objects``: at platform scope the scoped
    manager already returns every school, and using the unscoped one here would
    mean a capability bug presented as a cross-tenant leak instead of as a 404.
    """

    capability = Capability.MANAGE_SCHOOL_MODULES
    template_name = "core/platform_school.html"
    context_object_name = "school"

    def get_queryset(self):
        return School.objects.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.object
        User = get_user_model()

        context["modules"] = state_for_school(school.pk)
        context["always_on"] = [m for m in MODULES if m.always_on]

        branches = list(
            Branch.objects.filter(school=school)
            .annotate(
                students_count=Count(
                    "students",
                    filter=Q(students__status=StudentStatus.ACTIVE),
                    distinct=True,
                ),
                classes_count=Count(
                    "classes", filter=Q(classes__is_active=True), distinct=True
                ),
            )
            .select_related("head")
            .order_by("name")
        )
        # One query for every campus's current term, paired up here rather than
        # handed to the template as a dict: a Django template cannot look a dict
        # up by a variable key without a filter invented for the purpose.
        current_terms = {
            term.branch_id: term
            for term in Term.objects.filter(is_current=True, branch__school=school)
        }
        context["branches"] = branches
        context["rows"] = [
            {"branch": branch, "term": current_terms.get(branch.pk)}
            for branch in branches
        ]
        context["staff"] = (
            User.objects.filter(school=school)
            .select_related("branch")
            .order_by("role", "username")
        )
        context["student_count"] = Student.objects.filter(
            school=school, status=StudentStatus.ACTIVE
        ).count()
        context["class_count"] = Class.objects.filter(
            school=school, is_active=True
        ).count()
        # A pointer rather than a copy: the collections figures for this school
        # are a report, and the report already knows how to scope itself to one.
        context["report_url"] = f"{reverse('reports:index')}?school={school.pk}"
        context["page_title"] = school.name
        return context


class SchoolModuleToggleView(CapabilityRequiredMixin, View):
    """Switch one module on or off for one school.

    POST only, and it names both the module and the state it wants rather than
    flipping whatever it finds. A toggle that said "flip it" would do the wrong
    thing the moment somebody double-submitted, or opened the page in two tabs
    and pressed the switch in the stale one.

    ``SchoolModule.set_state`` refuses a key the registry does not know and one it
    says cannot be switched off, so a hand-made POST cannot record a decision that
    every reader would then have to ignore.
    """

    capability = Capability.MANAGE_SCHOOL_MODULES

    def post(self, request, pk, *args, **kwargs):
        school = get_object_or_404(School.objects, pk=pk)
        key = request.POST.get("module", "")
        enabled = request.POST.get("enabled") == "on"

        module = BY_KEY.get(key)
        if module is None or module.always_on:
            messages.error(
                request,
                "That is not a feature that can be switched." if module is None
                else f"{module.label} is part of the product and cannot be switched off.",
            )
            return HttpResponseRedirect(self._back(school))

        SchoolModule.set_state(school, key, enabled, by=request.user)
        messages.success(
            request,
            f"{module.label} is now {'on' if enabled else 'off'} for "
            f"{school.name}. Nothing has been deleted"
            f"{'' if enabled else ', and switching it back on restores every screen'}.",
        )
        return HttpResponseRedirect(self._back(school))

    def _back(self, school) -> str:
        return reverse("core:platform_school", args=[school.pk])


class SchoolDashboardView(RoleDashboardMixin, TemplateView):
    """A school owner's view: how big the school is, and where its fees stand.

    The campus-by-campus table this screen used to carry moved to Reports, where
    it sits beside the same breakdown per class, per method and per month. What
    stays is what a proprietor wants without asking for it -- three figures, one
    picture of the roster, and a warning when a campus cannot be billed at all.
    """

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

        context["branch_count"] = len(branches)
        context["student_count"] = sum(b.students_count for b in branches)
        context["class_count"] = sum(b.classes_count for b in branches)
        # An alert rather than a breakdown, so it stays: a campus with no current
        # term cannot be billed, and that is not something to go and look up.
        context["branches_without_a_term"] = [
            b.name for b in branches if b.pk not in current_terms
        ]
        # The one headline chart. `any current term` is the guard; the breakdown
        # itself resolves each campus's own term -- see status_breakdown.
        context["status"] = status_breakdown(next(iter(current_terms.values()), None))
        context["setup"] = setup_progress(self.request)
        context["page_title"] = "School overview"
        return context


class BranchDashboardView(RoleDashboardMixin, TemplateView):
    """A principal's campus: how big it is, and what nobody has priced.

    The class-by-class table moved to Reports. The *count* of unpriced classes
    did not, because it is not a breakdown -- it is children being carried on the
    roster with no fee against their name, which a principal should be told
    rather than have to go and find.
    """

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

        priced = set()
        if term is not None:
            priced = set(
                FeeStructure.objects.filter(term=term).values_list(
                    "school_class_id", flat=True
                )
            )
        # The setup gap a principal needs to see: children enrolled in a class
        # nobody has priced for the term they are being billed for.
        context["unpriced_classes"] = [
            klass
            for klass in Class.objects.filter(is_active=True)
            .annotate(
                students_count=Count(
                    "students",
                    filter=Q(students__status=StudentStatus.ACTIVE),
                    distinct=True,
                ),
            )
            .filter(students_count__gt=0)
            if klass.pk not in priced
        ]
        # The one headline chart, from the same derivation the bursar's screen
        # and the report both use.
        context["status"] = status_breakdown(term)
        context["setup"] = setup_progress(self.request)
        context["page_title"] = "Branch dashboard"
        return context


class BursarDashboardView(RoleDashboardMixin, TemplateView):
    """A bursar's view: what the term is worth, and how much of it is in.

    Read-only throughout, which matches the capability table -- a bursar sees
    every figure here and changes none of them from this screen.

    The class-by-class table this page used to end with moved to Reports, where
    it gained a collected and an outstanding column and the company of five more
    breakdowns. What stays is the three figures a bursar checks on arrival and the
    one picture of them -- plus the unpriced-class warning, which is a hole in the
    expected total rather than a breakdown of it, so it belongs next to the
    figure it undermines.
    """

    template_name = "core/dashboard_bursar.html"
    home_url_name = "core:bursar_dashboard"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        term = Term.objects.filter(is_current=True).first()
        context["term"] = term

        expected_total = ZERO
        if term is not None:
            structures = list(FeeStructure.objects.filter(term=term))
            # Two queries however many classes there are: one for the line-item
            # totals, one for the head counts.
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
                expected_total += totals.get(
                    structure.school_class_id, ZERO
                ) * head_counts.get(structure.school_class_id, 0)

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

        context["expected_total"] = expected_total
        context["student_count"] = Student.objects.filter(
            status=StudentStatus.ACTIVE
        ).count()
        context.update(
            with_outstanding(collection_summary(term), expected_total)
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
