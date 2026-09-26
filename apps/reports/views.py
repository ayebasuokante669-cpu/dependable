"""The two screens: the report, and the same report as a file.

Neither view computes anything. Both resolve the scope, ask
:func:`apps.reports.reporting.build` for a report, and hand it to a renderer --
the template in one case, :mod:`apps.reports.pdf` in the other. That is the whole
reason the download can be trusted: it is not a second implementation of the
page, it is the same structure drawn on paper.

**Who may read it.** ``VIEW_PAYMENTS``, which every role holds -- a bursar
collects against these figures, a principal and a proprietor answer for them.
What differs between roles is not the permission but the *scope*: the
tenant-scoped managers decide which campuses exist for the caller before any of
this runs, so a principal's report covers their campus, a proprietor's covers
their school, and the platform owner's covers the platform. One screen, and the
question it answers narrows itself.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.views.generic import TemplateView, View

from apps.core.permissions import Capability, CapabilityRequiredMixin

from . import pdf, reporting
from .forms import ReportScopeForm


class ReportAccessMixin(CapabilityRequiredMixin):
    """Signed in, and allowed to see money. Scope does the rest."""

    capability = Capability.VIEW_PAYMENTS

    def report_scope(self) -> reporting.ReportScope:
        if not hasattr(self, "_scope"):
            self._scope = reporting.resolve_scope(
                school_id=self.request.GET.get("school"),
                branch_id=self.request.GET.get("branch"),
            )
        return self._scope

    def build_report(self):
        who = self.request.user.get_full_name() or self.request.user.username
        return reporting.build(self.report_scope(), prepared_for=who)


class ReportsIndexView(ReportAccessMixin, TemplateView):
    """The collections report on screen."""

    template_name = "reports/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scope = self.report_scope()
        context["scope"] = scope
        context["report"] = self.build_report()
        context["scope_form"] = ReportScopeForm(
            data=self.request.GET or None, scope=scope
        )
        # The download has to cover what is on screen, so it carries the same
        # query string rather than a fresh one.
        context["pdf_query"] = self.request.GET.urlencode()
        context["page_title"] = "Reports"
        return context


class ReportPdfView(ReportAccessMixin, View):
    """The same report, as a file.

    ``Content-Disposition: attachment`` rather than ``inline``: the button says
    "Download as PDF", and a browser that opened it in a tab instead would have
    made the button a lie. The filename names the school, the report and the day
    it was generated, so a folder of these is readable without opening any of
    them.
    """

    def get(self, request, *args, **kwargs):
        report = self.build_report()
        response = HttpResponse(pdf.render(report), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{report.filename}"'
        return response
