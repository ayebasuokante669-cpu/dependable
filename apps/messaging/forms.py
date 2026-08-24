"""The compose screen's form.

A plain ``Form`` rather than a ``ModelForm``: what the user fills in is a body,
a channel and an audience *filter*, and the ``Message`` that results is built by
:mod:`apps.messaging.dispatch` out of the resolved audience. Binding the form
straight to the model would mean the stored audience description could disagree
with the students who were actually messaged.

Every queryset is tenant-scoped, so the classes and students on offer are
already only the caller's own. The cross-field checks below guard what scoping
cannot: a school owner who legitimately sees two campuses must still send one
message to one branch, because a batch belongs to a branch.
"""

from __future__ import annotations

from django import forms

from apps.academics.models import Class
from apps.core.forms import StyledFormMixin
from apps.schools.models import Branch
from apps.students.models import Student, StudentStatus

from .models import AudienceType
from .providers import Channel

#: Where an SMS starts costing a second segment. Not enforced -- a school may
#: have a good reason to send a long message -- but shown as it is typed,
#: because "why was I billed for three?" is a conversation nobody wants.
SMS_SEGMENT = 160


class ComposeForm(StyledFormMixin, forms.Form):
    """Write a message and choose who it goes to."""

    body = forms.CharField(
        label="Message",
        widget=forms.Textarea(
            attrs={
                "rows": 5,
                "placeholder": (
                    "Dear parent, this is a reminder that school fees for this "
                    "term are due."
                ),
                "maxlength": 1000,
            }
        ),
        max_length=1000,
    )
    channel = forms.ChoiceField(
        label="Send by",
        choices=Channel.choices,
        initial=Channel.SMS,
        widget=forms.RadioSelect,
    )
    audience_type = forms.ChoiceField(
        label="Send to",
        choices=AudienceType.choices,
        initial=AudienceType.ALL,
        widget=forms.RadioSelect,
    )
    school_class = forms.ModelChoiceField(
        label="Which class",
        queryset=Class.objects.none(),
        required=False,
        empty_label="Choose a class",
    )
    students = forms.ModelMultipleChoiceField(
        label="Which students",
        queryset=Student.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    branch = forms.ModelChoiceField(
        label="Campus",
        queryset=Branch.objects.none(),
        required=False,
        empty_label=None,
        help_text="A message belongs to one campus and reaches only its parents.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["school_class"].queryset = Class.objects.filter(
            is_active=True
        ).select_related("branch")
        self.fields["school_class"].label_from_instance = (
            lambda obj: obj.display_name
        )

        self.fields["students"].queryset = (
            Student.objects.filter(status=StudentStatus.ACTIVE)
            .select_related("school_class")
        )
        self.fields["students"].label_from_instance = (
            lambda obj: f"{obj.formal_name} — {obj.school_class.display_name}"
        )

        branches = Branch.objects.filter(is_active=True)
        self.branches = list(branches)
        self.fields["branch"].queryset = branches
        if len(self.branches) == 1:
            # A principal or bursar has exactly one campus and nothing to
            # decide, so the field is settled for them and hidden by the
            # template rather than asked as a one-option question.
            self.fields["branch"].initial = self.branches[0]

    @property
    def needs_branch_choice(self) -> bool:
        return len(self.branches) > 1

    def clean_body(self):
        # Collapse the trailing newline a textarea leaves behind: it costs a
        # character of a paid SMS segment and shows up as a blank line.
        return self.cleaned_data["body"].strip()

    def clean_branch(self):
        branch = self.cleaned_data.get("branch")
        if branch is None and len(self.branches) == 1:
            return self.branches[0]
        return branch

    def clean(self):
        cleaned = super().clean()
        audience_type = cleaned.get("audience_type")
        branch = cleaned.get("branch")

        if branch is None:
            self.add_error("branch", "Choose the campus this message is for.")

        if audience_type == AudienceType.CLASS and not cleaned.get("school_class"):
            self.add_error("school_class", "Choose the class to message.")
        if audience_type == AudienceType.STUDENTS and not cleaned.get("students"):
            self.add_error("students", "Choose at least one student.")

        # Scoping already limits these to the caller's own school; this catches
        # the school owner who can see two campuses picking a class from one
        # and a branch from the other.
        klass = cleaned.get("school_class")
        if branch and klass and klass.branch_id != branch.pk:
            self.add_error(
                "school_class", f"{klass.display_name} is not at {branch.name}."
            )
        students = cleaned.get("students")
        if branch and students:
            strays = [s for s in students if s.branch_id != branch.pk]
            if strays:
                self.add_error(
                    "students",
                    f"{strays[0].full_name} is not at {branch.name}. A message "
                    f"reaches one campus's parents only.",
                )
        return cleaned
