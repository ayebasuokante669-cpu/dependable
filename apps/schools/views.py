"""The school's own campuses.

These screens exist because the sidebar used to point "Branches" at the Django
admin, which only ``is_staff`` accounts can open -- so the one role that most
needs them, the proprietor who owns the school, was bounced to a login form on
a site they were already signed in to. The admin is also not tenant-scoped: an
account let into it would be one URL away from another school's rows.

Nothing here filters by school. ``Branch.objects`` is tenant-scoped, so the
list is already narrowed and ``get_object()`` 404s on another school's campus
rather than leaking it.
"""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Count
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from apps.core.permissions import Capability, CapabilityRequiredMixin

from .forms import BranchForm
from .models import Branch


class ReadBranchesMixin(CapabilityRequiredMixin):
    capability = Capability.VIEW_BRANCHES


class ManageBranchesMixin(CapabilityRequiredMixin):
    """Owner level only. A principal runs a campus; they do not open one."""

    capability = Capability.MANAGE_BRANCHES


class BranchListView(ReadBranchesMixin, ListView):
    model = Branch
    template_name = "schools/branch_list.html"
    context_object_name = "branches"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("school", "head")
            .annotate(
                class_count=Count("classes", distinct=True),
                student_count=Count("students", distinct=True),
            )
            .order_by("name")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Branches"
        return context


class BranchCreateView(ManageBranchesMixin, CreateView):
    model = Branch
    form_class = BranchForm
    template_name = "schools/branch_form.html"
    success_url = reverse_lazy("schools:branch_list")
    extra_context = {"page_title": "New branch", "verb": "Create"}

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"{self.object.name} added.")
        return response


class BranchUpdateView(ManageBranchesMixin, UpdateView):
    model = Branch
    form_class = BranchForm
    template_name = "schools/branch_form.html"
    success_url = reverse_lazy("schools:branch_list")
    extra_context = {"page_title": "Edit branch", "verb": "Save"}

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"{self.object.name} updated.")
        return response
