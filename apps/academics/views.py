"""Academic setup screens.

No view here filters by school or branch. It does not need to: ``Class.objects``
and ``Subject.objects`` are tenant-scoped managers, so list querysets are
already narrowed and ``get_object()`` returns 404 for another branch's row
rather than leaking it.
"""

from __future__ import annotations

from collections import OrderedDict

from django.contrib import messages
from django.db.models import Count, Prefetch
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from apps.core.permissions import Capability, CapabilityRequiredMixin

from .forms import ClassForm, SubjectForm
from .models import Class, Level, Subject


class ReadAcademicsMixin(CapabilityRequiredMixin):
    capability = Capability.VIEW_ACADEMICS


class ManageAcademicsMixin(CapabilityRequiredMixin):
    """Owner- and principal-level only. A bursar reaches these and gets a 403."""

    capability = Capability.MANAGE_ACADEMICS


class FlashOnSuccessMixin:
    success_message = ""

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.success_message:
            messages.success(self.request, self.success_message % {"object": self.object})
        return response


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------

class ClassListView(ReadAcademicsMixin, ListView):
    model = Class
    template_name = "academics/class_list.html"
    context_object_name = "classes"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("branch")
            .annotate(subject_count=Count("subjects", distinct=True))
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Grouped by band, in admission order, which is how a head of school
        # reads a class list.
        groups = OrderedDict()
        for klass in context["classes"]:
            groups.setdefault(klass.level, []).append(klass)
        context["level_groups"] = [
            {"label": Level(level).label, "classes": items}
            for level, items in groups.items()
        ]
        context["page_title"] = "Classes"
        return context


class ClassCreateView(ManageAcademicsMixin, FlashOnSuccessMixin, CreateView):
    model = Class
    form_class = ClassForm
    template_name = "academics/class_form.html"
    success_url = reverse_lazy("academics:class_list")
    success_message = "Class %(object)s created."
    extra_context = {"page_title": "New class", "verb": "Create"}


class ClassUpdateView(ManageAcademicsMixin, FlashOnSuccessMixin, UpdateView):
    model = Class
    form_class = ClassForm
    template_name = "academics/class_form.html"
    success_url = reverse_lazy("academics:class_list")
    success_message = "Class %(object)s updated."
    extra_context = {"page_title": "Edit class", "verb": "Save"}


class ClassDeleteView(ManageAcademicsMixin, DeleteView):
    model = Class
    template_name = "academics/class_confirm_delete.html"
    success_url = reverse_lazy("academics:class_list")
    extra_context = {"page_title": "Delete class"}

    def form_valid(self, form):
        messages.success(self.request, f"Class {self.object} deleted.")
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------

class SubjectListView(ReadAcademicsMixin, ListView):
    model = Subject
    template_name = "academics/subject_list.html"
    context_object_name = "subjects"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("branch")
            .prefetch_related(
                # Class.objects is scoped too, so the chips shown against each
                # subject cannot include another branch's classes.
                Prefetch("classes", queryset=Class.objects.all())
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Subjects"
        context["unassigned_count"] = sum(
            1 for s in context["subjects"] if not s.classes.all()
        )
        return context


class SubjectFormMixin:
    """Groups the class checkboxes by level so the list stays readable."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        bound = context["form"]["classes"]
        groups = OrderedDict()
        for widget, klass in zip(bound.subwidgets, bound.field.queryset):
            groups.setdefault(klass.level, []).append(widget)
        context["class_groups"] = [
            {"label": Level(level).label, "widgets": widgets}
            for level, widgets in groups.items()
        ]
        return context


class SubjectCreateView(
    ManageAcademicsMixin, SubjectFormMixin, FlashOnSuccessMixin, CreateView
):
    model = Subject
    form_class = SubjectForm
    template_name = "academics/subject_form.html"
    success_url = reverse_lazy("academics:subject_list")
    success_message = "Subject %(object)s created."
    extra_context = {"page_title": "New subject", "verb": "Create"}


class SubjectUpdateView(
    ManageAcademicsMixin, SubjectFormMixin, FlashOnSuccessMixin, UpdateView
):
    model = Subject
    form_class = SubjectForm
    template_name = "academics/subject_form.html"
    success_url = reverse_lazy("academics:subject_list")
    success_message = "Subject %(object)s updated."
    extra_context = {"page_title": "Edit subject", "verb": "Save"}


class SubjectDeleteView(ManageAcademicsMixin, DeleteView):
    model = Subject
    template_name = "academics/subject_confirm_delete.html"
    success_url = reverse_lazy("academics:subject_list")
    extra_context = {"page_title": "Delete subject"}

    def form_valid(self, form):
        messages.success(self.request, f"Subject {self.object} deleted.")
        return super().form_valid(form)
