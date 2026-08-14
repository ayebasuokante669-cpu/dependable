"""Forms for academic setup.

Every queryset here goes through a tenant-scoped manager, so the choices a user
is offered are already limited to what they may see. The extra validation below
guards the one thing scoping alone cannot: a school owner who *can* see two
branches must still not wire them together.
"""

from __future__ import annotations

from django import forms

# BranchScopedForm lives in core: fees needs the same behaviour.
from apps.core.forms import BranchScopedForm

from .models import Class, Subject


class ClassForm(BranchScopedForm):
    class Meta:
        model = Class
        fields = ["branch", "name", "level", "year_in_level", "stream", "is_active"]

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean_stream(self):
        return self.cleaned_data["stream"].strip()


class SubjectForm(BranchScopedForm):
    classes = forms.ModelMultipleChoiceField(
        queryset=Class.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Taught in",
        help_text="Tick every class this subject is taught in.",
    )

    class Meta:
        model = Subject
        fields = ["branch", "name", "code", "classes", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Scoped manager -> only classes this user may see are even offered.
        self.fields["classes"].queryset = Class.objects.filter(is_active=True)

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean(self):
        cleaned = super().clean()
        branch = cleaned.get("branch")
        classes = cleaned.get("classes")
        if branch and classes:
            stray = [c.display_name for c in classes if c.branch_id != branch.pk]
            if stray:
                raise forms.ValidationError(
                    {
                        "classes": "These classes belong to a different branch: "
                        + ", ".join(stray)
                    }
                )
        return cleaned
