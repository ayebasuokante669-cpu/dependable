"""Student management screens.

As with academics and fees, no view here filters by school or branch:
``Student.objects`` is a tenant-scoped manager, so the list is already narrowed
and another branch's student 404s rather than leaking.

What is new at this layer is the fee position shown against each student. It is
never read off the student -- :mod:`apps.students.fees` derives it from the
class's fee structure for the current term, in a fixed number of queries per
page.
"""

from __future__ import annotations

from django.contrib import messages
from django.db.models import Q
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from apps.academics.models import Class
from apps.core.permissions import Capability, CapabilityRequiredMixin

from . import fees
from .forms import StudentFilterForm, StudentForm
from .models import Student, StudentStatus


class ReadStudentsMixin(CapabilityRequiredMixin):
    """Every role reaches the roster; a bursar needs it to record payments."""

    capability = Capability.VIEW_STUDENTS


class ManageStudentsMixin(CapabilityRequiredMixin):
    """Owner- and principal-level only. A bursar reaches these and gets a 403."""

    capability = Capability.MANAGE_STUDENTS


class StudentListView(ReadStudentsMixin, ListView):
    """The branch roster: searchable, filterable, paginated.

    Twenty-five to a page: enough that a class fits on one screen, few enough
    that the fee lookup stays a fixed cost per page.
    """

    model = Student
    template_name = "students/student_list.html"
    context_object_name = "students"
    paginate_by = 25

    def get_filter_form(self) -> StudentFilterForm:
        if not hasattr(self, "_filter_form"):
            params = self.request.GET.copy()
            # An absent status means "the people actually here"; an explicitly
            # empty one means the caller asked for everybody.
            params.setdefault("status", StudentStatus.ACTIVE)
            self._filter_form = StudentFilterForm(data=params)
            self._filter_form.is_valid()
        return self._filter_form

    def get_queryset(self):
        queryset = super().get_queryset().select_related(
            "school_class", "branch"
        )
        form = self.get_filter_form()
        filters = form.cleaned_data if form.is_valid() else {}

        term = filters.get("q")
        if term:
            # Admission numbers are searched whole and by fragment, because
            # staff quote either "FA/2025/014" or just "014".
            queryset = queryset.filter(
                Q(first_name__icontains=term)
                | Q(last_name__icontains=term)
                | Q(other_names__icontains=term)
                | Q(admission_number__icontains=term)
            )
        if filters.get("school_class"):
            queryset = queryset.filter(school_class=filters["school_class"])
        if filters.get("status"):
            queryset = queryset.filter(status=filters["status"])
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page = list(context["students"])

        # Two queries for the whole page, however many classes it spans.
        schedule = fees.load(page)
        context["rows"] = [
            {"student": student, "position": schedule.position_for(student)}
            for student in page
        ]
        context["expected_on_page"] = sum(
            (row["position"].expected for row in context["rows"]), fees.ZERO
        )
        context["unpriced_on_page"] = sum(
            1 for row in context["rows"] if not row["position"].is_priced
        )
        context["current_terms"] = list(schedule.terms.values())

        context["filter_form"] = self.get_filter_form()
        context["is_filtered"] = any(
            self.request.GET.get(name) for name in ("q", "school_class", "status")
        )
        # Only asked when the filters found nothing, to tell the user whether the
        # roster is empty or their search was too narrow.
        if not page:
            context["total_count"] = Student.objects.count()
        context["page_title"] = "Students"
        return context


class StudentDetailView(ReadStudentsMixin, DetailView):
    model = Student
    template_name = "students/student_detail.html"
    context_object_name = "student"

    def get_queryset(self):
        return super().get_queryset().select_related("school_class", "branch", "school")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.object
        position = fees.position_for(student)
        context["position"] = position
        # The line items behind the expected figure: a parent asking "what am I
        # paying for?" is answered on this page, not by hopping to /fees/.
        context["fee_components"] = (
            list(position.structure.components.all()) if position.structure else []
        )
        context["classmate_count"] = (
            Student.objects.filter(
                school_class_id=student.school_class_id, status=StudentStatus.ACTIVE
            )
            .exclude(pk=student.pk)
            .count()
        )
        context["page_title"] = student.full_name
        return context


class StudentCreateView(ManageStudentsMixin, CreateView):
    model = Student
    form_class = StudentForm
    template_name = "students/student_form.html"
    extra_context = {"page_title": "New student", "verb": "Enrol"}

    def get_initial(self):
        initial = super().get_initial()
        # Arrive from a filtered list and the class is already chosen, so
        # enrolling a whole intake into one class is one field less each time.
        requested_class = self.request.GET.get("school_class")
        if requested_class:
            klass = Class.objects.filter(pk=requested_class).first()
            if klass:
                initial["school_class"] = klass
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(
            self.request,
            f"{self.object.full_name} enrolled in "
            f"{self.object.school_class.display_name} as "
            f"{self.object.admission_number}.",
        )
        return response

    def get_success_url(self):
        return self.object.get_absolute_url()


class StudentUpdateView(ManageStudentsMixin, UpdateView):
    model = Student
    form_class = StudentForm
    template_name = "students/student_form.html"
    extra_context = {"verb": "Save"}

    def get_queryset(self):
        return super().get_queryset().select_related("school_class", "branch")

    def get_initial(self):
        initial = super().get_initial()
        # "Withdraw instead" on the delete page lands here with the status
        # pre-selected. It is still only a suggestion until the form is saved.
        requested = self.request.GET.get("status")
        if requested in StudentStatus.values:
            initial["status"] = requested
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = f"Edit — {self.object.full_name}"
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, f"{self.object.full_name} updated.")
        return response

    def get_success_url(self):
        return self.object.get_absolute_url()


class StudentDeleteView(ManageStudentsMixin, DeleteView):
    model = Student
    template_name = "students/student_confirm_delete.html"
    success_url = reverse_lazy("students:student_list")
    extra_context = {"page_title": "Remove student"}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Offered on the confirmation page: withdrawing keeps the record and the
        # payment history that will hang off it, which is almost always what the
        # school actually means by "remove".
        context["withdraw_url"] = (
            f"{reverse('students:student_update', args=[self.object.pk])}"
            f"?status={StudentStatus.WITHDRAWN}"
        )
        return context

    def form_valid(self, form):
        messages.success(self.request, f"{self.object.full_name} removed from the roster.")
        return super().form_valid(form)
