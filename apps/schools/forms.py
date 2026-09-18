"""Forms for the school's own campuses."""

from __future__ import annotations

from django import forms

from apps.core.forms import StyledFormMixin
from apps.core.roles import Role

from .models import Branch


class BranchForm(StyledFormMixin, forms.ModelForm):
    """Add or edit a campus.

    The school is never a field. It comes from the signed-in account, so a
    proprietor cannot -- by editing the form in the browser -- file a campus
    under somebody else's school. A platform owner editing an existing branch
    keeps whatever school that branch already belongs to.
    """

    class Meta:
        model = Branch
        fields = ["name", "city", "state", "address", "head", "is_active"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2})}
        help_texts = {
            "is_active": "Clear this to retire a campus without deleting its records."
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # Only this school's own staff can head one of its campuses, and only
        # roles that run a campus are offered.
        head = self.fields["head"]
        head.queryset = head.queryset.filter(
            school=self._school(), role__in=[Role.SCHOOL_OWNER, Role.PRINCIPAL]
        )
        head.empty_label = "Not assigned"
        head.label = "Head of campus"

    def _school(self):
        if self.instance.pk and self.instance.school_id:
            return self.instance.school
        return getattr(self.user, "school", None)

    def save(self, commit=True):
        branch = super().save(commit=False)
        if not branch.school_id:
            branch.school = self._school()
        if commit:
            branch.save()
        return branch

    def clean(self):
        cleaned = super().clean()
        if self._school() is None:
            # A platform owner has no school of their own, so there is nothing
            # to file a new campus under. They add one from the admin, against
            # a named school.
            raise forms.ValidationError(
                "This account is not attached to a school, so it cannot add a "
                "campus. Add it from the school's own owner account."
            )
        return cleaned
