"""Fee structure management screens.

As with academics, no view filters by school or branch: the tenant-scoped
managers do it, so a list is already narrowed and a cross-branch lookup 404s.
"""

from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from apps.academics.models import Class, Level
from apps.core.permissions import Capability, CapabilityRequiredMixin

from .forms import FeeComponentFormSet, FeeStructureForm, NewFeeComponentFormSet
from .models import FeeStructure, Term


class ReadFeesMixin(CapabilityRequiredMixin):
    capability = Capability.VIEW_FEES


class ManageFeesMixin(CapabilityRequiredMixin):
    """Owner- and principal-level only. A bursar reaches these and gets a 403."""

    capability = Capability.MANAGE_FEES


class TermContextMixin:
    """Resolves which term the screen is about, from ``?term=`` or the current one."""

    _term_cache = False

    def get_terms(self):
        return Term.objects.select_related("branch")

    def get_selected_term(self):
        if self._term_cache is not False:
            return self._term_cache
        terms = self.get_terms()
        requested = self.request.GET.get("term")
        term = None
        if requested:
            term = terms.filter(pk=requested).first()
        if term is None:
            term = terms.filter(is_current=True).first() or terms.first()
        self._term_cache = term
        return term


class FeeStructureListView(ReadFeesMixin, TermContextMixin, ListView):
    model = FeeStructure
    template_name = "fees/structure_list.html"
    context_object_name = "structures"

    def get_queryset(self):
        term = self.get_selected_term()
        if term is None:
            return FeeStructure.objects.none()
        return (
            FeeStructure.objects.filter(term=term)
            .select_related("school_class", "term", "branch")
            .prefetch_related("components")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        term = self.get_selected_term()
        structures = list(context["structures"])

        groups = OrderedDict()
        for structure in structures:
            groups.setdefault(structure.school_class.level, []).append(structure)
        context["level_groups"] = [
            {
                "label": Level(level).label,
                "structures": items,
                "subtotal": sum((s.total for s in items), Decimal("0")),
            }
            for level, items in groups.items()
        ]

        context["terms"] = list(self.get_terms())
        context["selected_term"] = term
        context["grand_total"] = sum((s.total for s in structures), Decimal("0"))

        # Which classes at this branch still have no fees set for the term --
        # the question a school owner actually has during onboarding.
        if term is not None:
            context["classes_without_fees"] = list(
                Class.objects.filter(is_active=True, branch=term.branch)
                .exclude(pk__in=[s.school_class_id for s in structures])
            )
        else:
            context["classes_without_fees"] = []

        context["page_title"] = "Fee structures"
        return context


class ComponentFormsetMixin:
    """Drives the fee structure form and its line-item formset together.

    Both must validate before either is written, so a bad line item cannot
    leave a structure behind with no components.
    """

    updating = False
    formset_class = FeeComponentFormSet

    def get_formset(self, instance, data=None):
        return self.formset_class(data=data, instance=instance)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("formset", self.get_formset(self.object))
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object() if self.updating else None
        form = self.get_form()
        formset = self.get_formset(self.object or FeeStructure(), data=request.POST)

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                self.object = form.save()
                formset.instance = self.object
                formset.save()
                self._apply_positions(formset)
            messages.success(
                request,
                f"Fees for {self.object.school_class.display_name} "
                f"({self.object.term.name}) saved — total "
                f"₦{self.object.total:,.0f}.",
            )
            return HttpResponseRedirect(self.get_success_url())

        return self.render_to_response(
            self.get_context_data(form=form, formset=formset)
        )

    @staticmethod
    def _apply_positions(formset) -> None:
        """Persist the on-screen order of the line items."""
        position = 0
        for form in formset.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            component = form.instance
            if component.pk and component.position != position:
                component.position = position
                component.save(update_fields=["position"])
            position += 1

    def get_success_url(self):
        return f"{reverse('fees:structure_list')}?term={self.object.term_id}"


class FeeStructureCreateView(
    ManageFeesMixin, ComponentFormsetMixin, TermContextMixin, CreateView
):
    model = FeeStructure
    form_class = FeeStructureForm
    template_name = "fees/structure_form.html"
    updating = False
    formset_class = NewFeeComponentFormSet
    extra_context = {"page_title": "New fee structure", "verb": "Create"}

    def get_initial(self):
        initial = super().get_initial()
        # Arrive from "Set fees" on the list and the class is already chosen.
        requested_class = self.request.GET.get("class")
        if requested_class:
            klass = Class.objects.filter(pk=requested_class).first()
            if klass:
                initial["school_class"] = klass
        term = self.get_selected_term()
        if term:
            initial["term"] = term
        return initial


class FeeStructureUpdateView(ManageFeesMixin, ComponentFormsetMixin, UpdateView):
    model = FeeStructure
    form_class = FeeStructureForm
    template_name = "fees/structure_form.html"
    updating = True
    extra_context = {"verb": "Save"}

    def get_queryset(self):
        return super().get_queryset().select_related("school_class", "term")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = f"Fees — {self.object.school_class.display_name}"
        return context


class FeeStructureDeleteView(ManageFeesMixin, DeleteView):
    model = FeeStructure
    template_name = "fees/structure_confirm_delete.html"
    success_url = reverse_lazy("fees:structure_list")
    extra_context = {"page_title": "Delete fee structure"}

    def form_valid(self, form):
        label = f"{self.object.school_class.display_name} ({self.object.term.name})"
        messages.success(self.request, f"Fee structure for {label} deleted.")
        return super().form_valid(form)
