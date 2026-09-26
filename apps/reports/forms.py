"""The report's scope picker. Nothing here is saved.

Two selects, and both of them are built from what the *caller* can already see:
the choices come off a :class:`~apps.reports.reporting.ReportScope`, which was
resolved through the tenant-scoped managers. So the form cannot offer another
school's campus, and a hand-typed id that is not in the choices is not a
validation error -- ``resolve_scope`` has already treated it as "nothing chosen"
and produced the caller's own report. The form's job is only to show which
choice is in force.

A select with one option is not a choice, so it is dropped: a principal has one
campus and is offered no picker at all, and a proprietor with a single campus is
not asked which of it they meant.
"""

from __future__ import annotations

from django import forms


class ReportScopeForm(forms.Form):
    """Which school and campus the report covers."""

    school = forms.ChoiceField(required=False, label="School")
    branch = forms.ChoiceField(required=False, label="Campus")

    def __init__(self, *args, scope=None, **kwargs):
        super().__init__(*args, **kwargs)

        schools = list(getattr(scope, "schools", ()) or ())
        branches = list(getattr(scope, "choosable_branches", ()) or ())
        if scope is not None and scope.chosen_school is not None:
            # A campus list that spans schools would let the two selects
            # contradict each other. Choosing a school narrows the campuses to
            # it; choosing none leaves every campus the caller can see.
            branches = [
                b for b in branches if b.school_id == scope.chosen_school.pk
            ]

        self.fields["school"].choices = [("", "Every school")] + [
            (str(school.pk), school.name) for school in schools
        ]
        self.fields["branch"].choices = [("", "Every campus")] + [
            (str(branch.pk), branch.name) for branch in branches
        ]

        for name, options in (("school", schools), ("branch", branches)):
            if len(options) < 2:
                del self.fields[name]

        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "field-input")

    @property
    def is_useful(self) -> bool:
        """Whether there is any choice left to offer once the single-option
        selects have been dropped."""
        return bool(self.fields)
