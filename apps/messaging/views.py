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
from django.views.generic import DetailView, FormView, ListView, View

from apps.core.permissions import Capability, CapabilityRequiredMixin

from . import audiences
from .forms import SMS_SEGMENT, ComposeForm
from .models import AudienceType, Message, MessageRecipient
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

        provider = get_provider()
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


def _provider_banner() -> dict:
    """What the screens say about who is carrying these messages.

    Shown on both messaging screens, because "it said it sent" means something
    quite different on the console provider than on a paid gateway, and the
    school should never have to guess which one they are on.
    """
    provider = get_provider()
    return {
        "key": provider.key,
        "label": provider.label,
        "is_console": provider.key == "console",
        "is_configured": provider.is_configured,
        "channels": [Channel(c).label for c in provider.channels],
    }
