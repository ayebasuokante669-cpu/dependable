"""Student management screens.

As with academics and fees, no view here filters by school or branch:
``Student.objects`` is a tenant-scoped manager, so the list is already narrowed
and another branch's student 404s rather than leaking.

What is new at this layer is the fee position shown against each student. It is
never read off the student -- :mod:`apps.students.fees` derives it from the
class's fee structure for the current term, in a fixed number of queries per
page.

The Excel import is three screens rather than one: download the template,
upload and see the verdict, then confirm. The middle screen writes nothing, and
the parsed rows wait in the session between it and the confirmation, where they
are validated a *second* time against the live database -- the roster can have
changed while the report was on screen, and the report the user agreed to has to
be the one that gets written.
"""

from __future__ import annotations

from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from apps.academics.models import Class
from apps.core.permissions import Capability, CapabilityRequiredMixin
from apps.schools.models import Branch

from . import fees, importer, workbook
from .forms import StudentFilterForm, StudentForm, StudentImportForm
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


# ===========================================================================
# Excel import
# ===========================================================================

#: Where the parsed-but-unwritten rows wait between the report and the user
#: confirming it. A session key rather than a database table: an import that is
#: abandoned halfway should leave nothing behind to clean up, and the rows are
#: plain strings that survive the round trip untouched.
IMPORT_SESSION_KEY = "students.import"

XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


class StudentImportTemplateView(ManageStudentsMixin, View):
    """Download the blank template, built for the caller's own classes."""

    def get(self, request, *args, **kwargs):
        branch = _requested_branch(request)
        class_names = importer.ClassIndex(branch).names if branch else []
        content = workbook.build_template(class_names)

        response = HttpResponse(content, content_type=XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = (
            f'attachment; filename="{workbook.TEMPLATE_FILENAME}"'
        )
        response["Content-Length"] = len(content)
        return response


class StudentImportView(ManageStudentsMixin, FormView):
    """Step one and two: the instructions, and the file itself.

    A successful parse writes nothing -- it puts the rows in the session and
    redirects to the report, so a refresh of the review screen does not re-post
    the upload.
    """

    template_name = "students/student_import.html"
    form_class = StudentImportForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Import students"
        context["columns"] = importer.COLUMNS
        context["max_rows"] = importer.MAX_ROWS
        context["class_count"] = Class.objects.filter(is_active=True).count()

        # One download per campus when there is more than one: the template
        # carries that branch's class list, so "whichever branch came first"
        # would hand a school owner the wrong drop-down.
        branches = list(Branch.objects.filter(is_active=True))
        context["branches"] = branches
        context["template_links"] = [
            {
                "label": (
                    f"Download template — {branch.name}"
                    if len(branches) > 1 else "Download template (.xlsx)"
                ),
                "href": (
                    f"{reverse('students:student_import_template')}"
                    f"?branch={branch.pk}"
                ),
            }
            for branch in branches
        ]
        return context

    def form_valid(self, form):
        branch = form.chosen_branch
        if branch is None:
            form.add_error(None, "Choose the campus these students belong to.")
            return self.form_invalid(form)

        try:
            sheet = workbook.read_rows(form.cleaned_data["upload"])
        except workbook.WorkbookError as exc:
            # Everything the reader refuses arrives here as a sentence for the
            # user. A malformed workbook is a normal Tuesday, not a 500.
            form.add_error("upload", str(exc))
            return self.form_invalid(form)

        self.request.session[IMPORT_SESSION_KEY] = {
            "branch_id": branch.pk,
            "filename": form.cleaned_data["upload"].name,
            "sheet_name": sheet.sheet_name,
            "unknown_columns": sheet.unknown_columns,
            "absent_columns": sheet.absent_columns,
            "rows": [row.as_dict() for row in sheet.rows],
        }
        return HttpResponseRedirect(reverse("students:student_import_review"))


class StudentImportReviewView(ManageStudentsMixin, TemplateView):
    """Step three: the per-row verdict, and the button that writes it.

    Validation runs on GET *and* again on POST. Doing it twice is deliberate:
    the report the user is looking at was true when it was drawn, and the only
    way to be sure it is still true is to ask again with the writes about to
    happen.
    """

    template_name = "students/student_import_review.html"

    def get(self, request, *args, **kwargs):
        # Loaded here rather than in dispatch() so the capability check runs
        # first: a bursar gets the 403 they are owed, not a redirect.
        bounce = self._load_pending(request)
        return bounce or super().get(request, *args, **kwargs)

    def _load_pending(self, request):
        """Rehydrate the session payload, or bounce back to the upload screen."""
        payload = request.session.get(IMPORT_SESSION_KEY)
        if not payload:
            messages.info(request, "Upload a file to import students from.")
            return HttpResponseRedirect(reverse("students:student_import"))

        # Re-fetched through the scoped manager, so a session carried to another
        # account or a branch since removed cannot be imported into.
        branch = Branch.objects.filter(pk=payload["branch_id"]).first()
        if branch is None:
            del request.session[IMPORT_SESSION_KEY]
            messages.error(
                request, "That campus is no longer available. Start the import again."
            )
            return HttpResponseRedirect(reverse("students:student_import"))

        self.payload = payload
        self.branch = branch
        self.source_rows = [
            importer.SourceRow.from_dict(row) for row in payload["rows"]
        ]
        return None

    def build_report(self) -> importer.ImportReport:
        return importer.validate(
            self.source_rows,
            branch=self.branch,
            sheet_name=self.payload.get("sheet_name", ""),
            unknown_columns=self.payload.get("unknown_columns", []),
            absent_columns=self.payload.get("absent_columns", []),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        report = kwargs.get("report") or self.build_report()
        context["report"] = report
        context["branch"] = self.branch
        context["filename"] = self.payload.get("filename", "")
        context["page_title"] = "Check the import"
        context["show_branch"] = Branch.objects.count() > 1
        return context

    def post(self, request, *args, **kwargs):
        bounce = self._load_pending(request)
        if bounce is not None:
            return bounce

        if request.POST.get("action") == "discard":
            del request.session[IMPORT_SESSION_KEY]
            messages.info(request, "That file was discarded. Nothing was imported.")
            return HttpResponseRedirect(reverse("students:student_import"))

        report = self.build_report()
        if not report.has_anything_to_import:
            messages.error(
                request,
                "There is nothing to import — every row in that file still needs "
                "correcting.",
            )
            return self.render_to_response(self.get_context_data(report=report))

        try:
            created = importer.commit(report)
        except IntegrityError:
            # The roster moved under us between validating and writing. The
            # transaction rolled the whole batch back, so the file is still
            # exactly as it was and re-running the report is safe.
            messages.error(
                request,
                "The roster changed while that file was open, so nothing was "
                "imported. Check the report below and try again.",
            )
            return self.render_to_response(self.get_context_data())

        del request.session[IMPORT_SESSION_KEY]

        skipped = report.failed_count
        note = (
            f" {skipped} row{'' if skipped == 1 else 's'} "
            f"{'was' if skipped == 1 else 'were'} skipped and still "
            f"{'needs' if skipped == 1 else 'need'} correcting."
            if skipped else ""
        )
        messages.success(
            request,
            f"{len(created)} student{'' if len(created) == 1 else 's'} imported "
            f"into {self.branch.name}.{note}",
        )
        return HttpResponseRedirect(reverse("students:student_list"))


def _requested_branch(request) -> Branch | None:
    """The branch a template download is for.

    ``?branch=`` when the account can see several and picked one on the upload
    screen; otherwise the only one it can see. Always resolved through the
    scoped manager, so a guessed id gets the caller's own branch and not
    somebody else's class list.
    """
    branches = Branch.objects.filter(is_active=True)
    requested = request.GET.get("branch")
    if requested:
        branch = branches.filter(pk=requested).first()
        if branch is not None:
            return branch
    return branches.first()
