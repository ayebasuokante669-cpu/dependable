"""Messaging screens: compose, the fee-reminder shortcut, and the delivery log.

No view here filters by school or branch. ``Message.objects``,
``MessageRecipient.objects`` and ``Student.objects`` are tenant-scoped
managers, so a log is already narrowed and another branch's message 404s.

The live recipient count is a small JSON endpoint rather than a JS framework:
:class:`RecipientCountView` runs exactly the same :func:`audiences.resolve`
call that Send will run, so the number on screen cannot drift from the batch
that goes out. With scripting off, the count is still rendered on load and the
form still sends -- the endpoint only saves a round trip.
"""

from __future__ import annotations

from django.contrib import messages as flash
from django.db.models import Count, Q
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.views.generic import (
    DetailView,
    FormView,
    ListView,
    TemplateView,
    View,
)

from apps.core.permissions import Capability, CapabilityRequiredMixin

from . import audiences
from . import identity as identity_module
from .forms import SMS_SEGMENT, ComposeForm, MessagingIdentityForm
from .identity import SenderIdentityUnavailable
from .models import (
    AudienceType,
    Message,
    MessageRecipient,
    SchoolMessagingConfig,
    SenderIdStatus,
)
from .providers import Channel, DeliveryStatus, ProviderNotConfigured, get_provider


class ReadMessagesMixin(CapabilityRequiredMixin):
    capability = Capability.VIEW_MESSAGES


class SendMessagesMixin(CapabilityRequiredMixin):
    """Bursar, principal and owner alike -- chasing fees is the bursar's job."""

    capability = Capability.SEND_MESSAGES


# ===========================================================================
# The log
# ===========================================================================


class MessageListView(ReadMessagesMixin, ListView):
    """Every batch this branch has sent, newest first."""

    model = Message
    template_name = "messaging/message_list.html"
    context_object_name = "messages_sent"
    paginate_by = 25

    def get_queryset(self):
        # The delivery summary for the whole page in one query: counting per
        # row would be a query per message, and this screen exists to be
        # skimmed.
        return (
            super()
            .get_queryset()
            .select_related("sender", "branch")
            .annotate(
                total=Count("recipients"),
                delivered=Count(
                    "recipients",
                    filter=Q(recipients__status=DeliveryStatus.DELIVERED),
                ),
                sent=Count(
                    "recipients",
                    filter=Q(recipients__status__in=[
                        DeliveryStatus.SENT, DeliveryStatus.DELIVERED
                    ]),
                ),
                failed=Count(
                    "recipients", filter=Q(recipients__status=DeliveryStatus.FAILED)
                ),
            )
            # Aggregation drops the model's default ordering, and an unordered
            # queryset paginates inconsistently -- page two could repeat a row
            # from page one.
            .order_by("-created_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Messages"
        context["provider"] = _provider_banner()
        context["identity"] = _identity_banner(
            self.request.user.school_id, self.request.user.branch_id
        )
        context["can_send"] = "send_messages" in _capabilities(self.request)
        return context


class MessageDetailView(ReadMessagesMixin, DetailView):
    """One batch, and what happened to every parent's copy of it."""

    model = Message
    template_name = "messaging/message_detail.html"
    context_object_name = "message"

    def get_queryset(self):
        return super().get_queryset().select_related("sender", "branch")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        message = self.object
        context["recipients"] = list(
            MessageRecipient.objects.filter(message=message).select_related(
                "student", "student__school_class"
            )
        )
        counts = message.delivery_counts()
        context["counts"] = counts
        context["total"] = sum(counts.values())
        context["failures"] = [
            r for r in context["recipients"] if r.status == DeliveryStatus.FAILED
        ]
        context["page_title"] = f"Message to {message.audience}"
        return context


# ===========================================================================
# Compose
# ===========================================================================


class ComposeView(SendMessagesMixin, FormView):
    """Write a message, pick an audience, watch the count, send it.

    The GET side accepts ``?audience=`` and ``?class=`` so other screens can
    hand a bursar a pre-filled compose box -- which is all the fee-reminder
    shortcut below is.
    """

    template_name = "messaging/compose.html"
    form_class = ComposeForm

    #: Overridden by the reminder shortcut.
    preset_audience: str | None = None
    preset_body: str = ""
    heading = "New message"
    intro = ""

    def get_initial(self):
        initial = super().get_initial()
        requested = self.preset_audience or self.request.GET.get("audience")
        if requested in AudienceType.values:
            initial["audience_type"] = requested
        if self.preset_body:
            initial["body"] = self.preset_body

        requested_class = self.request.GET.get("class")
        if requested_class:
            # Through the scoped manager: a guessed id finds nothing rather
            # than another branch's class.
            from apps.academics.models import Class

            klass = Class.objects.filter(pk=requested_class).first()
            if klass is not None:
                initial["school_class"] = klass
                initial["audience_type"] = AudienceType.CLASS
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context["form"]
        context["page_title"] = self.heading
        context["heading"] = self.heading
        context["intro"] = self.intro
        context["provider"] = _provider_banner()
        # A school owner who has not picked a campus yet still gets their
        # school's identity: it is the same Sender ID either way unless they
        # have set up a per-campus one, and showing nothing would read as
        # "not configured".
        chosen_branch = _form_branch(form)
        context["identity"] = _identity_banner(
            getattr(chosen_branch, "school_id", None) or self.request.user.school_id,
            chosen_branch,
        )
        context["sms_segment"] = SMS_SEGMENT
        context["count_url"] = reverse("messaging:recipient_count")

        # The count shown before a single keystroke, so the screen is honest
        # with scripting switched off.
        context["preview"] = audiences.resolve(
            form["audience_type"].value() or AudienceType.ALL,
            branch=_form_branch(form),
            school_class=_field_object(form, "school_class"),
            students=_field_objects(form, "students"),
        )
        context["needs_branch_choice"] = form.needs_branch_choice
        return context

    def form_valid(self, form):
        data = form.cleaned_data
        branch = data["branch"]
        audience = audiences.resolve(
            data["audience_type"],
            branch=branch,
            school_class=data.get("school_class"),
            students=list(data.get("students") or []),
        )

        if not audience:
            form.add_error(
                None,
                "That audience has nobody in it with a phone number on file, so "
                "there is nothing to send.",
            )
            return self.form_invalid(form)

        # Who this school sends as. Resolved before anything is written, so a
        # school with no approved Sender ID keeps its draft and gets told why
        # rather than producing a batch that could never have gone out.
        try:
            identity = identity_module.resolve_for_branch(branch)
        except SenderIdentityUnavailable as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        provider = get_provider(identity=identity)
        try:
            provider.check()
        except ProviderNotConfigured as exc:
            # A missing API key is a settings problem, not a form error the
            # user can fix by retyping -- say so plainly and keep their draft.
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        from . import dispatch

        message = dispatch.send(
            body=data["body"],
            channel=data["channel"],
            audience=audience,
            branch=branch,
            sender=self.request.user,
            provider=provider,
        )

        counts = message.delivery_counts()
        succeeded = counts[DeliveryStatus.SENT] + counts[DeliveryStatus.DELIVERED]
        failed = counts[DeliveryStatus.FAILED]
        note = f" {failed} failed." if failed else ""
        flash.success(
            self.request,
            f"{Channel(message.channel).label} sent to {succeeded} "
            f"parent{'' if succeeded == 1 else 's'} — {message.audience}.{note}",
        )
        if audience.unreachable_count:
            flash.warning(
                self.request,
                f"{audience.unreachable_count} "
                f"student{'' if audience.unreachable_count == 1 else 's'} in that "
                f"audience {'has' if audience.unreachable_count == 1 else 'have'} "
                f"no parent phone number on file and could not be messaged.",
            )
        self.message = message
        return HttpResponseRedirect(message.get_absolute_url())


class FeeReminderView(ComposeView):
    """The shortcut that ties messaging to the fees data.

    Not a second screen -- the same compose box with the audience already set
    to the parents who owe and a draft in the body. Fee chasing is the reason
    this feature exists, and a bursar should reach it in one click rather than
    by remembering which filter means "owing".
    """

    preset_audience = AudienceType.OWING
    heading = "Fee reminder"
    intro = (
        "Goes to the parents of students whose confirmed payments do not yet "
        "cover their class's fees for the current term. Classes with no fee "
        "structure this term are left out — there is no balance to chase."
    )
    preset_body = (
        "Dear parent, our records show an outstanding balance on your child's "
        "school fees for this term. Please visit the bursary to settle it. "
        "Thank you."
    )


class RecipientCountView(SendMessagesMixin, View):
    """``GET ?audience_type=...`` → ``{"count": 32, ...}``.

    Runs the identical resolution the send path runs, which is the whole point:
    the number on the button is the batch that will go out, not a second
    estimate of it.
    """

    def get(self, request, *args, **kwargs):
        from apps.academics.models import Class
        from apps.schools.models import Branch
        from apps.students.models import Student, StudentStatus

        audience_type = request.GET.get("audience_type", AudienceType.ALL)
        if audience_type not in AudienceType.values:
            audience_type = AudienceType.ALL

        # Every lookup goes through a scoped manager, so a guessed id resolves
        # to None and the count comes back zero -- never another branch's.
        branch = Branch.objects.filter(pk=_int(request.GET.get("branch"))).first()
        klass = Class.objects.filter(
            pk=_int(request.GET.get("school_class"))
        ).first()
        students = list(
            Student.objects.filter(
                pk__in=[_int(v) for v in request.GET.getlist("students")],
                status=StudentStatus.ACTIVE,
            )
        )

        audience = audiences.resolve(
            audience_type, branch=branch, school_class=klass, students=students
        )
        return JsonResponse(
            {
                "count": audience.count,
                "unreachable": audience.unreachable_count,
                "description": audience.description,
            }
        )


# ===========================================================================
# Messaging identity
# ===========================================================================


class ViewIdentityMixin(CapabilityRequiredMixin):
    capability = Capability.VIEW_MESSAGING_IDENTITY


class ManageIdentityMixin(CapabilityRequiredMixin):
    """Registering and approving a Sender ID is a platform action.

    The platform holds the gateway account and is the party that actually
    submits a school's Sender ID for registration, so approving one is not
    something a school can do for itself -- it would be marking its own
    homework, and the gateway would reject the first message anyway.
    """

    capability = Capability.MANAGE_MESSAGING_IDENTITY


class MessagingIdentityView(ViewIdentityMixin, TemplateView):
    """What a school sends as -- and, for the platform, every school's.

    One screen serving two readers, because they are asking the same question
    at different scopes: a proprietor wants "what do my parents see?", and the
    platform owner wants "which schools are registered and which are stuck?".
    Splitting them into two URLs would mean two templates saying the same thing.
    """

    template_name = "messaging/identity.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        can_manage = "manage_messaging_identity" in _capabilities(self.request)

        context["can_manage"] = can_manage
        context["provider"] = _provider_banner()
        context["page_title"] = "Messaging identity"

        if user.school_id:
            # A school account: its own identity, and nobody else's. Scoping
            # does the work -- this queryset cannot reach another tenant.
            context["is_platform_view"] = False
            context["configs"] = list(
                SchoolMessagingConfig.objects.select_related(
                    "school", "branch", "approved_by"
                )
            )
            context["school"] = user.school
            context["active"] = identity_module.config_for(
                user.school_id, user.branch
            )
            context["blocked_reason"] = _identity_block_reason(
                user.school, user.branch
            )
            return context

        # Platform staff have no school of their own, so they get the roll.
        context["is_platform_view"] = True
        context["rows"] = _platform_identity_rows()
        return context


class MessagingIdentityUpdateView(ManageIdentityMixin, FormView):
    """Register, edit or approve one school's Sender ID.

    Addressed by school rather than by config id, because the row very often
    does not exist yet: "set up Dap Group's Sender ID" and "fix Dap Group's
    Sender ID" are the same errand and should be the same URL.
    """

    template_name = "messaging/identity_form.html"
    form_class = MessagingIdentityForm

    def get_school(self):
        from django.shortcuts import get_object_or_404

        from apps.schools.models import School

        if not hasattr(self, "_school"):
            self._school = get_object_or_404(School.objects, pk=self.kwargs["pk"])
        return self._school

    def get_config(self) -> SchoolMessagingConfig | None:
        """The school-wide row. Per-branch identities are a later screen."""
        return SchoolMessagingConfig.all_objects.filter(
            school=self.get_school(), branch__isnull=True
        ).first()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_config()
        kwargs["school"] = self.get_school()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school()
        config = self.get_config()
        context["school"] = school
        context["config"] = config
        context["is_new"] = config is None
        context["page_title"] = (
            f"Sender ID — {school.name}" if config else f"Register — {school.name}"
        )
        return context

    def form_valid(self, form):
        config = form.save(user=self.request.user)
        verb = "registered" if form.was_created else "updated"
        if config.is_approved:
            flash.success(
                self.request,
                f'{config.school.name} {verb}. Messages now go out as '
                f'"{config.sender_id}" via {config.get_provider_display()}.',
            )
        else:
            flash.warning(
                self.request,
                f'{config.school.name} {verb}, but "{config.sender_id}" is '
                f"{config.get_status_display().lower()} — nothing will send "
                f"under it until it is approved.",
            )
        return HttpResponseRedirect(reverse("messaging:identity"))


# ===========================================================================
# Helpers
# ===========================================================================

def _int(value) -> int:
    """A querystring id as an int, or 0 -- which matches no row."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _field_object(form, name):
    """The single object a bound-or-initial model field points at, if any."""
    value = form[name].value()
    if not value:
        return None
    if hasattr(value, "pk"):
        return value
    return form.fields[name].queryset.filter(pk=_int(value)).first()


def _field_objects(form, name) -> list:
    """The objects a bound-or-initial multiple-choice field points at."""
    values = form[name].value() or []
    if hasattr(values, "pk"):
        values = [values]
    ids = [v.pk if hasattr(v, "pk") else _int(v) for v in values]
    return list(form.fields[name].queryset.filter(pk__in=ids)) if ids else []


def _form_branch(form):
    """The campus the compose form is currently pointed at.

    With one campus the field is settled by the form itself, so this is simply
    that branch; with several it is whatever the user has picked, and ``None``
    until they pick -- which shows a count of nobody rather than a count that
    silently spans both campuses.
    """
    return _field_object(form, "branch")


def _capabilities(request) -> set[str]:
    from apps.core.permissions import capabilities_of

    return {c.value for c in capabilities_of(request.user)}


def _identity_banner(school, branch=None) -> dict:
    """What the messaging screens say about who this school sends as.

    Takes ids as happily as instances, so the log screen can ask without
    dereferencing ``request.user.branch`` and paying a query for a row it does
    not otherwise need.

    Never raises: these are read-only screens, and a school with no Sender ID
    should see an explanation on the page rather than a 500. The refusal itself
    happens on the send path.
    """
    if school is None:
        return {"resolved": False, "reason": "", "sender_id": ""}
    try:
        identity = identity_module.resolve(school, branch)
    except SenderIdentityUnavailable as exc:
        return {
            "resolved": False,
            "reason": str(exc),
            "sender_id": getattr(exc.config, "sender_id", ""),
            "status": getattr(exc.config, "status", ""),
        }
    return {
        "resolved": True,
        "reason": "",
        "sender_id": identity.sender_id,
        "provider_label": _provider_label(identity.provider_key),
        "own_credentials": identity.uses_own_credentials,
    }


def _identity_block_reason(school, branch) -> str:
    try:
        identity_module.resolve(school, branch)
    except SenderIdentityUnavailable as exc:
        return str(exc)
    return ""


def _provider_label(key: str) -> str:
    from .providers import ProviderKey

    try:
        return ProviderKey(key).label
    except ValueError:
        return key


def _platform_identity_rows() -> list[dict]:
    """Every school and where its Sender ID stands, registered or not.

    Schools without a config are listed too -- a roll that only shows the ones
    already set up hides exactly the schools the platform owner needs to act on.
    """
    from apps.schools.models import School

    configs = {
        config.school_id: config
        for config in SchoolMessagingConfig.all_objects.filter(
            branch__isnull=True
        ).select_related("school", "approved_by")
    }
    return [
        {"school": school, "config": configs.get(school.pk)}
        for school in School.objects.all()
    ]


def _provider_banner() -> dict:
    """What the screens say about who is carrying these messages.

    Shown on both messaging screens, because "it said it sent" means something
    quite different on the console provider than on a paid gateway, and the
    school should never have to guess which one they are on.
    """
    from .providers import ProviderKey, SenderIdentity, platform_override

    override = platform_override()
    # Built with a placeholder identity so check() tests only the half this
    # banner is about: the platform's master credentials. Whether a given
    # school may send is its Sender ID's business -- see _identity_banner --
    # and reporting "not configured" here for a missing one would blame the
    # platform for a school's approval.
    provider = get_provider(identity=SenderIdentity(sender_id="probe"))
    return {
        "key": provider.key,
        "label": provider.label,
        "is_console": provider.key == ProviderKey.CONSOLE,
        "is_configured": provider.is_configured,
        "channels": [Channel(c).label for c in provider.channels],
        # True when every school is forced onto one gateway rather than using
        # the one on its own messaging config.
        "is_override": bool(override),
    }
