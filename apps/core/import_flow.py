"""The three screens every bulk import has, written once.

Download a template, upload the filled-in file, read the per-row verdict and
decide. That sequence is identical for students, classes, subjects and fee
structures, and so is almost everything about it: the capability check, the
session hand-off between the upload and the report, re-validating on confirm,
and the refusal to write anything until the user has pressed the button.

What differs per entity is small and declarative, and lives in an
:class:`ImportFlow` subclass: which sheet, what the file belongs to (a campus
for students and classes, a term for fee structures), and the three passes --
build the template, validate the rows, write the ones that passed.

The session, not a table
------------------------
Parsed-but-unwritten rows wait in the session between the report and the user
confirming it. An import abandoned halfway should leave nothing behind to clean
up, and the rows are plain strings that survive the round trip untouched.

Validated twice, deliberately
-----------------------------
The report the user is looking at was true when it was drawn. The only way to
be sure it is still true is to ask again, with the writes about to happen --
somebody else may have enrolled that admission number, or renamed that class,
in the meantime.
"""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.db import IntegrityError
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.views.generic import FormView, TemplateView, View

from .forms import StyledFormMixin
from .imports import ImportReport, Sheet, SourceRow
from .permissions import Capability, CapabilityRequiredMixin
from .spreadsheets import Choice, ParsedSheet, Reference, WorkbookError, build_template

XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


# ---------------------------------------------------------------------------
# The upload form
# ---------------------------------------------------------------------------


class ScopedUploadForm(StyledFormMixin, forms.Form):
    """Say what the file belongs to, hand over the workbook.

    The scope is asked for only when the account can see more than one --
    a principal has exactly one campus and should not be made to confirm it.
    It is asked at all because the import stamps everything it creates with
    that scope, and "whichever one the class happened to match" is not a
    decision to make on the user's behalf when the same class names run at two
    campuses.

    The queryset comes from a tenant-scoped manager, so the options offered are
    already only what this account may see. Dropping the field when there is
    one option is not cosmetic: it means the POST cannot be edited to name
    something the account cannot see.
    """

    #: Well above any real roster once zipped, and low enough that a mistaken
    #: upload is refused before it is read into memory.
    MAX_BYTES = 5 * 1024 * 1024

    upload = forms.FileField(
        label="Filled-in template",
        help_text="An .xlsx file. Use the template above so the headings match.",
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"}),
    )

    def __init__(self, *args, flow=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.flow = flow
        # Named after the thing it chooses -- "branch", "term" -- rather than
        # "scope". The field name is in the URL as ``?branch=`` and in anyone's
        # saved bookmark of the template download, and a generic name here would
        # have made the two disagree.
        self.scope_name = flow.scope_field_name
        options = list(flow.scope_queryset())

        if len(options) == 1:
            # Nothing to choose. The field is left out rather than hidden, so
            # the POST cannot be edited to name something the account cannot
            # see.
            self.only_scope = options[0]
        else:
            self.only_scope = None
            self.fields[self.scope_name] = forms.ModelChoiceField(
                queryset=flow.scope_queryset(),
                label=flow.scope_label,
                help_text=flow.scope_help,
                empty_label=None,
            )
            self.fields[self.scope_name].widget.attrs.setdefault(
                "class", "field-input"
            )
            self.order_fields([self.scope_name, "upload"])

    @property
    def scope_field(self):
        """The bound scope field, or ``None`` when there was nothing to ask."""
        return self[self.scope_name] if self.scope_name in self.fields else None

    @property
    def chosen_scope(self):
        """What the file is for, however it was decided."""
        if self.only_scope is not None:
            return self.only_scope
        return self.cleaned_data.get(self.scope_name)

    def clean_upload(self):
        upload = self.cleaned_data["upload"]
        name = (upload.name or "").lower()
        if not name.endswith(".xlsx"):
            raise forms.ValidationError(
                "That is not an .xlsx file. Open it in Excel or Google Sheets "
                "and save it as an Excel Workbook (.xlsx) — .xls and .csv files "
                "cannot be read here."
            )
        if upload.size and upload.size > self.MAX_BYTES:
            raise forms.ValidationError(
                f"That file is {upload.size / 1_048_576:.1f} MB. The import "
                f"accepts files up to "
                f"{self.MAX_BYTES // 1_048_576} MB — a file this large is "
                f"usually a spreadsheet with images or extra sheets in it."
            )
        return upload


# ---------------------------------------------------------------------------
# One import, described
# ---------------------------------------------------------------------------


class ImportFlow:
    """Everything one entity's bulk import needs that the screens do not know.

    Subclasses are stateless and cheap; a view builds one per request rather
    than holding a module-level instance, because the scope queryset is
    tenant-scoped and must be evaluated inside the request.
    """

    sheet: Sheet = None
    capability: Capability = None
    form_class = ScopedUploadForm

    #: Where the parsed rows wait. Distinct per entity, so two imports started
    #: in two tabs cannot overwrite each other's file.
    session_key = ""

    #: URL names: the template download, the upload screen, the report, and
    #: where a finished import lands.
    url_template = ""
    url_upload = ""
    url_review = ""
    url_done = ""

    upload_template_name = "core/import_upload.html"
    review_template_name = "core/import_review.html"

    #: Copy. Everything a screen says about *this* entity rather than about
    #: importing in general.
    heading = "Import"
    review_heading = "Check the import"
    intro = ""
    scope_label = "Campus"
    scope_help = ""
    #: The form field and query-string parameter the scope is chosen by. Named
    #: after what it is -- ``branch``, ``term`` -- so a template-download link
    #: reads as what it does.
    scope_field_name = "branch"
    no_scope_title = "Nothing to import into"
    no_scope_body = ""
    back_label = "Back"

    # -- the scope ---------------------------------------------------------

    def scope_queryset(self):
        """The things this file could belong to, tenant-scoped."""
        raise NotImplementedError

    def requested_scope(self, request):
        """The scope a template download is for.

        ``?branch=`` (or ``?term=``) when the account can see several and picked
        one on the upload screen; otherwise the only one it can see. Always
        resolved through the scoped manager, so a guessed id gets the caller's
        own data and not somebody else's class list.
        """
        available = self.scope_queryset()
        requested = request.GET.get(self.scope_field_name)
        if requested:
            chosen = available.filter(pk=requested).first()
            if chosen is not None:
                return chosen
        return available.first()

    def branch_for(self, scope):
        """The campus the records will be stamped with."""
        return scope

    def done_url(self, scope=None) -> str:
        """Where a finished -- or abandoned -- import lands.

        Takes the scope because a screen that is *about* one, like the fee list
        with its term selector, should open on the one just imported into rather
        than on whichever the selector defaults to.
        """
        return reverse(self.url_done)

    def describe_scope(self, scope) -> str:
        """What to call the scope on screen.

        ``name`` rather than ``str()``: a ``Branch`` prints as "Alpha Academy -
        North", and a screen already inside one school does not need to be told
        which school it is in.
        """
        return getattr(scope, "name", None) or str(scope)

    def template_links(self, request) -> list[dict]:
        """One download per scope when there is more than one.

        The template carries that campus's class list, so "whichever came
        first" would hand a school owner the wrong drop-down.
        """
        options = list(self.scope_queryset())
        base = reverse(self.url_template)
        if len(options) <= 1:
            return [{"label": "Download template (.xlsx)", "href": base}]
        return [
            {
                "label": f"Download template — {self.describe_scope(scope)}",
                "href": f"{base}?{self.scope_field_name}={scope.pk}",
            }
            for scope in options
        ]

    # -- pass one: the template --------------------------------------------

    def choices(self) -> tuple[Choice, ...]:
        """Short drop-downs written into the file itself."""
        return ()

    def references(self, scope) -> tuple[Reference, ...]:
        """Lists on their own sheet -- the branch's real classes, and so on."""
        return ()

    def build_template(self, scope) -> bytes:
        return build_template(
            self.sheet,
            choices=self.choices(),
            references=self.references(scope) if scope is not None else (),
        )

    # -- pass two: the verdict ---------------------------------------------

    def read(self, upload) -> ParsedSheet:
        from .spreadsheets import read_rows

        return read_rows(upload, self.sheet)

    def validate(self, rows: list[SourceRow], scope, **parsed) -> ImportReport:
        raise NotImplementedError

    # -- pass three: the write ---------------------------------------------

    def commit(self, report: ImportReport, scope) -> list:
        raise NotImplementedError

    def success_message(self, created: list, report: ImportReport, scope) -> str:
        count = len(created)
        noun = self.sheet.noun if count == 1 else self.sheet.noun_plural
        skipped = report.failed_count
        note = (
            f" {skipped} row{'' if skipped == 1 else 's'} "
            f"{'was' if skipped == 1 else 'were'} skipped and still "
            f"{'needs' if skipped == 1 else 'need'} correcting."
            if skipped else ""
        )
        return (
            f"{count} {noun} imported into "
            f"{self.describe_scope(scope)}.{note}"
        )

    def nothing_to_import_message(self) -> str:
        return (
            "There is nothing to import — every row in that file still needs "
            "correcting."
        )

    def conflict_message(self) -> str:
        return (
            "The data changed while that file was open, so nothing was "
            "imported. Check the report below and try again."
        )

    # -- what the screens show ---------------------------------------------

    def upload_context(self, request) -> dict:
        """Anything the upload screen needs beyond the shared scaffolding."""
        return {}

    def review_context(self, report: ImportReport, scope) -> dict:
        return {}


# ---------------------------------------------------------------------------
# The three screens
# ---------------------------------------------------------------------------


class ImportStepView(CapabilityRequiredMixin):
    """Shared plumbing: the flow, and the capability it is gated on."""

    flow_class: type[ImportFlow] = None

    @property
    def capability(self):
        return self.flow_class.capability

    def get_flow(self) -> ImportFlow:
        if not hasattr(self, "_flow"):
            self._flow = self.flow_class()
        return self._flow


class ImportTemplateView(ImportStepView, View):
    """Download the blank template, built for the caller's own data."""

    def get(self, request, *args, **kwargs):
        flow = self.get_flow()
        scope = flow.requested_scope(request)
        content = flow.build_template(scope)

        response = HttpResponse(content, content_type=XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = (
            f'attachment; filename="{flow.sheet.filename}"'
        )
        response["Content-Length"] = len(content)
        return response


class ImportUploadView(ImportStepView, FormView):
    """Step one and two: the instructions, and the file itself.

    A successful parse writes nothing -- it puts the rows in the session and
    redirects to the report, so a refresh of the review screen does not re-post
    the upload.
    """

    def get_template_names(self):
        return [self.get_flow().upload_template_name]

    def get_form_class(self):
        return self.get_flow().form_class

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["flow"] = self.get_flow()
        return kwargs

    def get_context_data(self, **kwargs):
        flow = self.get_flow()
        context = super().get_context_data(**kwargs)
        context.update(
            flow=flow,
            sheet=flow.sheet,
            columns=flow.sheet.columns,
            max_rows=flow.sheet.max_rows,
            page_title=flow.heading,
            intro=flow.intro,
            scopes=list(flow.scope_queryset()),
            template_links=flow.template_links(self.request),
            review_url=reverse(flow.url_review),
            done_url=flow.done_url(),
        )
        context.update(flow.upload_context(self.request))
        return context

    def form_valid(self, form):
        flow = self.get_flow()
        scope = form.chosen_scope
        if scope is None:
            form.add_error(None, f"Choose the {flow.scope_label.lower()} first.")
            return self.form_invalid(form)

        try:
            sheet = flow.read(form.cleaned_data["upload"])
        except WorkbookError as exc:
            # Everything the reader refuses arrives here as a sentence for the
            # user. A malformed workbook is a normal Tuesday, not a 500.
            form.add_error("upload", str(exc))
            return self.form_invalid(form)

        self.request.session[flow.session_key] = {
            "scope_id": scope.pk,
            "filename": form.cleaned_data["upload"].name,
            "sheet_name": sheet.sheet_name,
            "unknown_columns": sheet.unknown_columns,
            "absent_columns": sheet.absent_columns,
            "rows": [row.as_dict() for row in sheet.rows],
        }
        return HttpResponseRedirect(reverse(flow.url_review))


class ImportReviewView(ImportStepView, TemplateView):
    """Step three: the per-row verdict, and the button that writes it."""

    def get_template_names(self):
        return [self.get_flow().review_template_name]

    def get(self, request, *args, **kwargs):
        # Loaded here rather than in dispatch() so the capability check runs
        # first: a bursar gets the 403 they are owed, not a redirect.
        bounce = self._load_pending(request)
        return bounce or super().get(request, *args, **kwargs)

    def _load_pending(self, request):
        """Rehydrate the session payload, or bounce back to the upload screen."""
        flow = self.get_flow()
        payload = request.session.get(flow.session_key)
        if not payload:
            messages.info(
                request, f"Upload a file to import {flow.sheet.noun_plural} from."
            )
            return HttpResponseRedirect(reverse(flow.url_upload))

        # Re-fetched through the scoped manager, so a session carried to another
        # account, or a campus since removed, cannot be imported into.
        scope = flow.scope_queryset().filter(pk=payload["scope_id"]).first()
        if scope is None:
            del request.session[flow.session_key]
            messages.error(
                request,
                f"That {flow.scope_label.lower()} is no longer available. "
                f"Start the import again.",
            )
            return HttpResponseRedirect(reverse(flow.url_upload))

        self.payload = payload
        self.scope = scope
        self.source_rows = [SourceRow.from_dict(row) for row in payload["rows"]]
        return None

    def build_report(self) -> ImportReport:
        return self.get_flow().validate(
            self.source_rows,
            self.scope,
            sheet_name=self.payload.get("sheet_name", ""),
            unknown_columns=self.payload.get("unknown_columns", []),
            absent_columns=self.payload.get("absent_columns", []),
        )

    def get_context_data(self, **kwargs):
        flow = self.get_flow()
        context = super().get_context_data(**kwargs)
        report = kwargs.get("report") or self.build_report()
        context.update(
            flow=flow,
            sheet=flow.sheet,
            report=report,
            scope=self.scope,
            scope_label=flow.describe_scope(self.scope),
            show_scope=flow.scope_queryset().count() > 1,
            filename=self.payload.get("filename", ""),
            page_title=flow.review_heading,
            upload_url=reverse(flow.url_upload),
            done_url=flow.done_url(self.scope),
        )
        context.update(flow.review_context(report, self.scope))
        return context

    def post(self, request, *args, **kwargs):
        flow = self.get_flow()
        bounce = self._load_pending(request)
        if bounce is not None:
            return bounce

        if request.POST.get("action") == "discard":
            del request.session[flow.session_key]
            messages.info(request, "That file was discarded. Nothing was imported.")
            return HttpResponseRedirect(reverse(flow.url_upload))

        report = self.build_report()
        if not report.has_anything_to_import:
            messages.error(request, flow.nothing_to_import_message())
            return self.render_to_response(self.get_context_data(report=report))

        try:
            created = flow.commit(report, self.scope)
        except IntegrityError:
            # The data moved under us between validating and writing. The
            # transaction rolled the whole batch back, so the file is still
            # exactly as it was and re-running the report is safe.
            messages.error(request, flow.conflict_message())
            return self.render_to_response(self.get_context_data())

        del request.session[flow.session_key]
        messages.success(
            request, flow.success_message(created, report, self.scope)
        )
        return HttpResponseRedirect(flow.done_url(self.scope))
