"""Form helpers that keep hand-written forms on the design system."""

from __future__ import annotations

from django import forms

from apps.schools.models import Branch


class StyledFormMixin:
    """Apply the design-system input classes to every widget on a form.

    Saves repeating ``widget=forms.TextInput(attrs={"class": "field-input"})``
    on every field, and means a token change lands everywhere at once.
    ``setdefault`` is used so a field can still opt out by setting its own class.
    """

    #: Widgets that must not receive the text-input styling.
    _UNSTYLED = (
        forms.CheckboxInput,
        forms.CheckboxSelectMultiple,
        forms.RadioSelect,
        forms.FileInput,
        forms.HiddenInput,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, self._UNSTYLED):
                if isinstance(widget, forms.CheckboxInput):
                    widget.attrs.setdefault("class", "field-checkbox")
                continue
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("class", "field-textarea")
                widget.attrs.setdefault("rows", 3)
            else:
                widget.attrs.setdefault("class", "field-input")


class BranchScopedForm(StyledFormMixin, forms.ModelForm):
    """Shared branch handling for records that belong to one campus.

    ``Branch.objects`` is tenant-scoped, so the choices are already limited to
    what the user may see. A principal has exactly one branch, so the field is
    pre-selected and there is nothing to decide; a school owner must pick.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "branch" in self.fields:
            branches = Branch.objects.filter(is_active=True)
            self.fields["branch"].queryset = branches
            self.fields["branch"].empty_label = None
            if self.instance.pk is None and len(branches) == 1:
                self.fields["branch"].initial = branches[0]

    @property
    def visible_branch_count(self) -> int:
        """How many branches this user can see -- drives whether labels need
        to be disambiguated by branch name."""
        return Branch.objects.count()
