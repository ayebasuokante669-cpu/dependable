"""Student entry -- one at a time, and by the spreadsheet.

``StudentForm`` is the path for the child who walks in on a Tuesday;
``StudentImportForm`` is the front door of the bulk import, and does nothing
except decide which campus the file belongs to and refuse anything that is
obviously not a workbook. The real reading happens in
:mod:`apps.students.workbook`.

Every queryset here goes through a tenant-scoped manager, so the classes offered
are already limited to what the user may see -- the extra validation below
guards what scoping alone cannot: a school owner who *can* see two branches must
still not put a Main Campus child into an Annex class.
"""

from __future__ import annotations

from django import forms

from apps.academics.models import Class
from apps.core.forms import StyledFormMixin
from apps.schools.models import Branch

from .models import Student, StudentStatus
from .validators import normalise_admission_number, normalise_phone


class StudentForm(StyledFormMixin, forms.ModelForm):
    """Add or edit one student.

    Branch is not a field: it comes from the chosen class, which always knows
    its own campus. Asking for both invites the two to disagree.
    """

    class Meta:
        model = Student
        fields = [
            "school_class",
            "admission_number",
            "first_name",
            "last_name",
            "other_names",
            "sex",
            "date_of_birth",
            "date_admitted",
            "status",
            "parent_name",
            "parent_phone",
            "parent_email",
            "address",
        ]
        widgets = {
            "admission_number": forms.TextInput(
                attrs={"placeholder": "FA/2025/001", "autocomplete": "off"}
            ),
            "first_name": forms.TextInput(attrs={"placeholder": "Chinaza"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Okonkwo"}),
            "other_names": forms.TextInput(attrs={"placeholder": "Adaeze"}),
            # `type=date` gets the native picker on mobile, which is where the
            # front desk will actually type this.
            "date_of_birth": forms.DateInput(
                attrs={"type": "date"}, format="%Y-%m-%d"
            ),
            "date_admitted": forms.DateInput(
                attrs={"type": "date"}, format="%Y-%m-%d"
            ),
            "parent_name": forms.TextInput(attrs={"placeholder": "Mrs. Ngozi Okonkwo"}),
            "parent_phone": forms.TextInput(
                attrs={
                    "placeholder": "0803 123 4567",
                    "inputmode": "tel",
                    "autocomplete": "tel",
                }
            ),
            "parent_email": forms.EmailInput(
                attrs={"placeholder": "parent@example.com", "autocomplete": "email"}
            ),
            "address": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        classes = Class.objects.filter(is_active=True).select_related("branch")
        # An existing student's class may since have been deactivated; keep it
        # selectable so editing their phone number does not force a class change.
        if self.instance.pk and self.instance.school_class_id:
            classes = classes | Class.objects.filter(pk=self.instance.school_class_id)
        self.fields["school_class"].queryset = classes.distinct()
        self.fields["school_class"].label = "Class"
        self.fields["school_class"].empty_label = "Select a class"

        # A school owner sees several branches at once, so a bare "JSS 1A" is
        # ambiguous. A principal sees one campus and does not need the noise.
        if Class.objects.values("branch_id").distinct().count() > 1:
            self.fields["school_class"].label_from_instance = (
                lambda obj: f"{obj.branch.name} — {obj.display_name}"
            )
        else:
            self.fields["school_class"].label_from_instance = (
                lambda obj: obj.display_name
            )

        self.fields["sex"].empty_label = "Select"
        self.fields["other_names"].help_text = "Middle names, if any."

    # -- normalisation -------------------------------------------------------

    def clean_admission_number(self):
        return normalise_admission_number(self.cleaned_data["admission_number"])

    def clean_parent_phone(self):
        """Store one canonical form, whatever shape it was typed in."""
        return normalise_phone(self.cleaned_data["parent_phone"])

    def clean_first_name(self):
        return self.cleaned_data["first_name"].strip()

    def clean_last_name(self):
        return self.cleaned_data["last_name"].strip()

    def clean_other_names(self):
        return self.cleaned_data["other_names"].strip()

    def clean_parent_name(self):
        return self.cleaned_data["parent_name"].strip()

    # -- cross-field ---------------------------------------------------------

    def clean(self):
        cleaned = super().clean()
        school_class = cleaned.get("school_class")
        admission_number = cleaned.get("admission_number")

        if school_class:
            # Set before the model's own clean() runs, so the branch check it
            # does has something to compare against.
            self.instance.branch = school_class.branch
            self.instance.school_id = school_class.school_id

        if school_class and admission_number:
            # Caught here as well as by the database constraint, so the user
            # gets the message on the field rather than a 500.
            clash = Student.objects.filter(
                branch=school_class.branch, admission_number__iexact=admission_number
            )
            if self.instance.pk:
                clash = clash.exclude(pk=self.instance.pk)
            existing = clash.first()
            if existing is not None:
                self.add_error(
                    "admission_number",
                    f"{existing.full_name} at {school_class.branch.name} already "
                    f"has that admission number.",
                )
        return cleaned


class StudentFilterForm(forms.Form):
    """The list screen's search and filter bar.

    A plain ``Form``, not a ModelForm: nothing here is saved, and every field is
    optional so an empty submit means "show everything".
    """

    q = forms.CharField(
        required=False,
        label="Search",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Name or admission number",
                "type": "search",
                "autocomplete": "off",
            }
        ),
    )
    school_class = forms.ModelChoiceField(
        queryset=Class.objects.none(),
        required=False,
        label="Class",
        empty_label="All classes",
    )
    status = forms.ChoiceField(
        required=False,
        label="Status",
        # "" is every status, including the ones that mean the student has left.
        # The view defaults the *absent* parameter to Active instead, because a
        # roster that has been running for years is mostly former students.
        choices=[("", "All statuses")] + list(StudentStatus.choices),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["school_class"].queryset = Class.objects.select_related("branch")
        self.fields["school_class"].label_from_instance = lambda obj: obj.display_name
        for field in self.fields.values():
            widget = field.widget
            widget.attrs.setdefault("class", "field-input")
            if isinstance(widget, forms.Select):
                # Submitting on change keeps the filters usable without pressing
                # anything; the noscript button on the form still works.
                widget.attrs.setdefault("onchange", "this.form.submit()")

    def clean_q(self):
        return self.cleaned_data["q"].strip()


class StudentImportForm(StyledFormMixin, forms.Form):
    """Pick the campus, hand over the workbook.

    Branch is asked for only when the account can see more than one -- a
    principal has exactly one campus and should not be made to confirm it. It is
    asked at all because the import stamps every student it creates with that
    branch, and "whichever branch the class happened to match" is not a decision
    to make on the user's behalf when the same class names run at two campuses.
    """

    #: Well above any real roster once zipped, and low enough that a mistaken
    #: upload is refused before it is read into memory.
    MAX_BYTES = 5 * 1024 * 1024

    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        label="Campus",
        empty_label=None,
        help_text="Every student in the file is enrolled at this branch.",
    )
    upload = forms.FileField(
        label="Filled-in template",
        help_text="An .xlsx file. Use the template above so the headings match.",
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active = Branch.objects.filter(is_active=True)
        self.fields["branch"].queryset = active
        branches = list(active)
        if len(branches) == 1:
            # Nothing to choose. The field is dropped rather than hidden so the
            # POST cannot be edited to name a branch the account cannot see.
            del self.fields["branch"]
            self.only_branch = branches[0]
        else:
            self.only_branch = None

    @property
    def chosen_branch(self) -> Branch | None:
        """The campus the file is for, however it was decided."""
        if self.only_branch is not None:
            return self.only_branch
        return self.cleaned_data.get("branch")

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
                f"{self.MAX_BYTES // 1_048_576} MB — a roster this large is "
                f"usually a spreadsheet with images or extra sheets in it."
            )
        return upload
