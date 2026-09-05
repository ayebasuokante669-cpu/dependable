"""The forms an admission passes through, in the order it passes through them.

Two of them are worth reading before the rest.

:class:`PublicEnquiryForm` is the only form in the app -- and the second on the
platform, after signup -- that runs with **no tenant context at all**. A parent
arriving from an Instagram link is anonymous, so every scoped manager here
would return nothing: an empty branch list, an empty class list, and a
uniqueness check that cannot see the rows it is checking against. Every
queryset it touches therefore goes through ``all_objects`` and is narrowed by
hand to the school in the URL. That narrowing is the security boundary, and it
is why the school is a constructor argument rather than anything the browser
can send.

:class:`ApplicationForm` is the one that makes "captured once" true. It is a
ModelForm over the *same row* the enquiry created, so every field the parent
already gave is on the instance and simply not on this form. What it adds is
the application-stage fields and whichever documents this applicant's level
actually requires -- built per instance from the requirement profile, so a
Nursery application never grows an entrance-exam field and a JSS one never
loses its transfer letter.
"""

from __future__ import annotations

from django import forms
from django.utils import timezone

from apps.academics.models import Class, Level
from apps.core.forms import StyledFormMixin
from apps.schools.models import Branch
from apps.students.validators import normalise_phone

from .models import (
    AdmissionPayment,
    AdmissionsConfig,
    Applicant,
    ApplicantSource,
    ApplicantStatus,
    Assessment,
    AssessmentOutcome,
)
from .requirements import DOCUMENT_KINDS, profile_for

#: The enquiry fields, in the order both enquiry forms ask for them. Declared
#: once so the public form and the walk-in form cannot drift apart -- they
#: write to the same row, and a field that only one of them collects would
#: quietly mean two grades of enquiry.
ENQUIRY_FIELDS = [
    "first_name",
    "last_name",
    "other_names",
    "sex",
    "date_of_birth",
    "school_class",
    "parent_name",
    "parent_phone",
    "parent_email",
    "previous_school",
    "heard_about",
]

ENQUIRY_WIDGETS = {
    "first_name": forms.TextInput(attrs={"placeholder": "First name"}),
    "last_name": forms.TextInput(attrs={"placeholder": "Surname"}),
    "other_names": forms.TextInput(attrs={"placeholder": "Middle names"}),
    # type=date gets the native picker, which is where a parent on a phone
    # will actually be typing this.
    "date_of_birth": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    "parent_name": forms.TextInput(attrs={"placeholder": "Parent or guardian name"}),
    "parent_phone": forms.TextInput(
        attrs={"placeholder": "0803 123 4567", "inputmode": "tel", "autocomplete": "tel"}
    ),
    "parent_email": forms.EmailInput(
        attrs={"placeholder": "parent@example.com", "autocomplete": "email"}
    ),
    "previous_school": forms.TextInput(
        attrs={"placeholder": "The school they attend now, if any"}
    ),
}


class _EnquiryFormBase(StyledFormMixin, forms.ModelForm):
    """What both enquiry forms share: the fields, and how a class is labelled."""

    class Meta:
        model = Applicant
        fields = ENQUIRY_FIELDS
        widgets = ENQUIRY_WIDGETS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sex"].empty_label = "Select"
        self.fields["school_class"].label = "Class applying for"
        self.fields["school_class"].empty_label = "Select a class"
        self.fields["school_class"].required = True
        self.fields["parent_email"].required = True
        self.fields["other_names"].required = False
        self.fields["previous_school"].required = False
        self.fields["date_of_birth"].required = True
        self.fields["heard_about"].required = False

    def _label_classes(self, *, show_branch: bool) -> None:
        field = self.fields["school_class"]
        if show_branch:
            field.label_from_instance = (
                lambda obj: f"{obj.branch.name} — {obj.display_name}"
            )
        else:
            field.label_from_instance = lambda obj: obj.display_name

    def clean_parent_phone(self):
        """Store one canonical form, whatever shape it was typed in."""
        return normalise_phone(self.cleaned_data["parent_phone"])

    def clean_first_name(self):
        return " ".join(self.cleaned_data["first_name"].split())

    def clean_last_name(self):
        return " ".join(self.cleaned_data["last_name"].split())

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get("date_of_birth")
        if dob and dob > timezone.localdate():
            raise forms.ValidationError("That date is in the future.")
        return dob


class PublicEnquiryForm(_EnquiryFormBase):
    """The form on the school's own public page. Anonymous, top-of-funnel only.

    Light on purpose: a parent deciding whether to enquire at all will abandon
    a form that asks for a birth certificate. Everything heavier is added at
    the application stage, by which point they have chosen the school.

    ``branch`` is a real field here and required, because the parent is outside
    the system and nothing else can know which campus they mean -- unlike the
    walk-in form, where the member of staff's own branch answers it.
    """

    branch = forms.ModelChoiceField(
        queryset=Branch.all_objects.none(),
        label="Campus",
        empty_label="Select a campus",
        help_text="Which of our campuses are you enquiring about?",
    )

    class Meta(_EnquiryFormBase.Meta):
        # Branch sits directly above the class it constrains.
        fields = ENQUIRY_FIELDS[:5] + ["branch"] + ENQUIRY_FIELDS[5:]

    def __init__(self, *args, school=None, **kwargs):
        self.school = school
        super().__init__(*args, **kwargs)

        # all_objects throughout: see the module docstring. The filter to
        # `self.school` is what keeps one school's form off another's data, and
        # it comes from the URL rather than from anything posted.
        branches = Branch.all_objects.filter(school=school, is_active=True).order_by("name")
        classes = (
            Class.all_objects.filter(branch__school=school, is_active=True)
            .select_related("branch")
            .order_by("branch__name", "level", "year_in_level", "stream")
        )
        self.fields["branch"].queryset = branches
        self.fields["school_class"].queryset = classes

        # One campus is not a choice, so it is made for them and stays visible
        # as confirmation rather than as a question.
        if len(branches) == 1:
            self.fields["branch"].initial = branches[0]
            self.fields["branch"].empty_label = None

        # Without a picker to filter the list, a bare "JSS 1A" is ambiguous the
        # moment a school runs two campuses.
        self._label_classes(show_branch=len(branches) > 1)

        self.fields["parent_email"].help_text = (
            "We will email you a reference for this enquiry, and let you know "
            "what happens next."
        )
        self.fields["heard_about"].label = "How did you hear about us?"
        self.fields["heard_about"].required = True

    def clean(self):
        cleaned = super().clean()
        branch = cleaned.get("branch")
        klass = cleaned.get("school_class")
        if branch and klass and klass.branch_id != branch.pk:
            self.add_error(
                "school_class",
                f"{klass.display_name} is not taught at {branch.name}. "
                f"Pick a class at the campus you selected.",
            )
        return cleaned

    def save(self, commit=True):
        applicant = super().save(commit=False)
        # Set from the URL's school, never from the post body -- this is the
        # line that makes "the public form only writes to the school in its
        # URL" true.
        applicant.school = self.school
        applicant.branch = self.cleaned_data["branch"]
        applicant.level = self.cleaned_data["school_class"].level
        applicant.source = ApplicantSource.ONLINE
        applicant.status = ApplicantStatus.ENQUIRY
        if commit:
            applicant.save()
        return applicant


class StaffEnquiryForm(_EnquiryFormBase):
    """The same enquiry, taken over the counter. Writes to the same model.

    The client's ask was to stop the front desk keeping a paper book that
    somebody later types up. So a walk-in is captured here and is from that
    moment indistinguishable from an online one -- same pipeline, same window,
    same reminder -- except for ``source``, which is worth keeping because
    "how many of our enquiries walk in?" is a real question.

    Branch is not asked for: it is the member of staff's own. A school owner,
    who has no single campus, is the one exception and gets the field.
    """

    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        label="Campus",
        empty_label="Select a campus",
        required=False,
    )

    class Meta(_EnquiryFormBase.Meta):
        fields = ENQUIRY_FIELDS[:5] + ["branch"] + ENQUIRY_FIELDS[5:] + ["notes"]
        widgets = {
            **ENQUIRY_WIDGETS,
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        # Branch.objects is tenant-scoped, so this is already the caller's own
        # school -- and, for a principal, their own campus.
        branches = Branch.objects.filter(is_active=True).order_by("name")
        self.staff_branch = getattr(user, "branch", None)

        if self.staff_branch is not None:
            # Auto-set, per the brief: the person at the desk is standing in
            # the campus the parent walked into.
            del self.fields["branch"]
        else:
            self.fields["branch"].queryset = branches
            self.fields["branch"].required = True
            self.fields["branch"].help_text = (
                "Your account covers every campus, so this one has to be said."
            )
            if len(branches) == 1:
                self.fields["branch"].initial = branches[0]

        classes = (
            Class.objects.filter(is_active=True)
            .select_related("branch")
            .order_by("branch__name", "level", "year_in_level", "stream")
        )
        if self.staff_branch is not None:
            classes = classes.filter(branch=self.staff_branch)
        self.fields["school_class"].queryset = classes
        self._label_classes(show_branch=self.staff_branch is None and len(branches) > 1)

        # A parent at the counter may not have their email to hand, and the
        # front desk should not be blocked on it -- unlike the public form,
        # where they are typing it themselves anyway.
        self.fields["parent_email"].required = False
        self.fields["parent_email"].help_text = (
            "Optional here, but without it the reminder and the decision "
            "cannot be emailed."
        )
        self.fields["notes"].label = "Notes"
        self.fields["notes"].required = False
        self.fields["heard_about"].label = "How did they hear about us?"

    def clean(self):
        cleaned = super().clean()
        branch = self.staff_branch or cleaned.get("branch")
        klass = cleaned.get("school_class")
        if branch and klass and klass.branch_id != branch.pk:
            self.add_error(
                "school_class", "That class belongs to a different campus."
            )
        return cleaned

    def save(self, commit=True):
        applicant = super().save(commit=False)
        applicant.branch = self.staff_branch or self.cleaned_data["branch"]
        applicant.school_id = applicant.branch.school_id
        applicant.level = self.cleaned_data["school_class"].level
        applicant.source = ApplicantSource.WALK_IN
        applicant.status = ApplicantStatus.ENQUIRY
        if commit:
            applicant.save()
        return applicant


class ApplicationForm(StyledFormMixin, forms.ModelForm):
    """The enquiry becoming an application: what it *adds*, and nothing else.

    Every field the parent already gave -- name, date of birth, class, contact
    details -- is on the instance and deliberately absent from this form. That
    is what "progressing adds fields rather than re-collecting them" means in
    practice: there is no place on this screen to retype any of it, so there is
    no place for the two copies to disagree.

    The document fields are assembled per instance from the applicant's
    requirement profile. A Nursery application asks for a photo, a birth
    certificate and an immunisation record; a JSS one asks for previous results
    and a transfer letter as well. Neither is written down in this class --
    both come from :mod:`apps.admissions.requirements`, which is where a school
    changes them.
    """

    class Meta:
        model = Applicant
        fields = [
            "school_class",
            "address",
            "nationality",
            "state_of_origin",
            "parent_occupation",
            "notes",
            *DOCUMENT_KINDS,
        ]
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "nationality": forms.TextInput(attrs={"placeholder": "Nigerian"}),
            "state_of_origin": forms.TextInput(attrs={"placeholder": "Enugu"}),
            "parent_occupation": forms.TextInput(attrs={"placeholder": "Trader"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        applicant = self.instance
        self.profile = applicant.requirement_profile

        self.fields["school_class"].queryset = Class.objects.filter(
            branch_id=applicant.branch_id, is_active=True
        )
        self.fields["school_class"].label = "Class applying for"
        self.fields["school_class"].empty_label = "Select a class"
        self.fields["school_class"].required = True
        self.fields["school_class"].label_from_instance = lambda obj: obj.display_name
        self.fields["school_class"].help_text = (
            "Carried over from the enquiry. Change it if the family has since "
            "asked for a different class."
        )

        wanted = {line.kind: line for line in self.profile.documents}
        for kind in DOCUMENT_KINDS:
            line = wanted.get(kind)
            if line is None:
                # Not asked for at this level -- removed rather than rendered as
                # a field the school would wonder why it was seeing. This is
                # what makes a Nursery application a different form from a JSS
                # one without a single `if level ==` in this class.
                del self.fields[kind]
                continue
            field = self.fields[kind]
            # Never hard-required, even when the requirement is compulsory. A
            # front desk types the application while the parent goes to
            # photocopy the birth certificate, and a form that refuses to save
            # until every paper is in hand would send them back to the paper
            # book this feature exists to replace.
            #
            # "Required" is therefore enforced where it actually matters --
            # `applicant.missing_requirements`, which the detail screen lists
            # and the decision screen shows the principal before they decide.
            field.required = False
            field.help_text = (
                f"Required for {self.profile.level_label}. You can save without "
                f"it and add it later."
                if line.is_required
                else "Optional at this level."
            )

        for name in ("address", "nationality", "state_of_origin", "parent_occupation"):
            self.fields[name].required = False
        self.fields["notes"].required = False
        self.fields["notes"].label = "Notes"

    @property
    def document_fields(self):
        """The document half of the form, each paired with its requirement line.

        Paired rather than bare, because the fields are deliberately not
        ``required`` (see ``__init__``) and the template still has to mark the
        compulsory ones. Reading ``field.field.required`` there would show no
        asterisk at all.
        """
        wanted = {line.kind: line for line in self.profile.documents}
        return [
            (self[kind], wanted[kind])
            for kind in DOCUMENT_KINDS
            if kind in self.fields
        ]

    @property
    def detail_fields(self):
        """Everything that is not a document, in declaration order."""
        return [
            self[name]
            for name in (
                "school_class",
                "address",
                "nationality",
                "state_of_origin",
                "parent_occupation",
                "notes",
            )
        ]

    def save(self, commit=True):
        applicant = super().save(commit=False)
        if applicant.school_class_id:
            # The class may have moved band -- Primary 1 to Nursery for a child
            # placed down a year -- and the requirements follow the level, so
            # it has to move with it.
            applicant.level = applicant.school_class.level
        if applicant.status == ApplicantStatus.ENQUIRY:
            applicant.status = ApplicantStatus.APPLICATION
            applicant.applied_at = timezone.now()
        if commit:
            applicant.save()
        return applicant


class AssessmentForm(StyledFormMixin, forms.ModelForm):
    """Schedule the entrance exam, then record what happened.

    One form for both, because they are the same row a fortnight apart: the
    school books a date, and later comes back and fills in the score. Splitting
    them would mean a second screen whose only job is to add two fields.
    """

    class Meta:
        model = Assessment
        fields = ["scheduled_for", "sat_on", "score", "max_score", "outcome", "notes"]
        widgets = {
            "scheduled_for": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "sat_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["scheduled_for"].help_text = "The date they are due to sit it."
        self.fields["sat_on"].help_text = "Leave blank until they have sat it."
        self.fields["max_score"].label = "Out of"
        for name in ("scheduled_for", "sat_on", "score", "notes"):
            self.fields[name].required = False

    def clean(self):
        cleaned = super().clean()
        outcome = cleaned.get("outcome")
        score = cleaned.get("score")
        maximum = cleaned.get("max_score")
        if score is not None and maximum and score > maximum:
            self.add_error("score", f"The score cannot be more than {maximum:g}.")
        if outcome in (AssessmentOutcome.PASSED, AssessmentOutcome.FAILED):
            if score is None:
                self.add_error(
                    "score", "Record the score before saying whether they passed."
                )
            if not cleaned.get("sat_on"):
                # Recorded rather than demanded twice: the date they sat is
                # almost always today, and making staff type it is friction.
                cleaned["sat_on"] = timezone.localdate()
        return cleaned


class DecisionForm(StyledFormMixin, forms.Form):
    """Offer a place, or refuse one. The step only a principal or owner reaches.

    A plain form rather than a ModelForm: the decision writes four fields on
    the applicant and may send an email, which is a service call rather than
    a field, so the view does the work and this collects the intent.
    """

    OFFER = ApplicantStatus.OFFERED
    REJECT = ApplicantStatus.REJECTED

    decision = forms.ChoiceField(
        choices=(
            (OFFER, "Offer a place"),
            (REJECT, "Do not offer a place"),
        ),
        widget=forms.RadioSelect,
        label="Decision",
    )
    decision_note = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        label="Note",
        help_text="The school's own words. Included in the email to the parent.",
    )
    notify_parent = forms.BooleanField(
        required=False,
        initial=True,
        label="Email the parent",
        help_text="Sends the decision to the address on the application.",
    )

    def __init__(self, *args, applicant=None, **kwargs):
        self.applicant = applicant
        super().__init__(*args, **kwargs)
        if applicant is not None and not applicant.parent_email:
            # Nothing to send to, so do not offer to send it.
            self.fields["notify_parent"].initial = False
            self.fields["notify_parent"].disabled = True
            self.fields["notify_parent"].help_text = (
                "No email address on this application, so nothing can be sent. "
                "Ring the parent instead."
            )


class EnrolmentForm(StyledFormMixin, forms.Form):
    """The conversion form: the number, the class, and the date they start.

    Not a ModelForm over ``Student``: every other field of the student is
    copied from the applicant by :func:`apps.admissions.enrolment.enrol`, and a
    form that offered them for editing would be inviting the roster and the
    application to disagree on the day they are created.
    """

    admission_number = forms.CharField(
        max_length=32,
        label="Admission number",
        help_text="Suggested from this branch's numbering. Change it if your "
        "school numbers differently.",
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    school_class = forms.ModelChoiceField(
        queryset=Class.objects.none(),
        label="Class",
        empty_label=None,
        help_text="Usually the class they applied for. Change it if they have "
        "been placed elsewhere after the assessment.",
    )
    date_admitted = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        label="Date admitted",
    )

    def __init__(self, *args, applicant=None, **kwargs):
        self.applicant = applicant
        super().__init__(*args, **kwargs)
        self.fields["school_class"].queryset = Class.objects.filter(
            branch_id=applicant.branch_id, is_active=True
        )
        self.fields["school_class"].label_from_instance = lambda obj: obj.display_name


class AdmissionPaymentForm(StyledFormMixin, forms.ModelForm):
    """Money taken at the door, against an applicant who is not yet a student."""

    class Meta:
        model = AdmissionPayment
        fields = ["amount", "method", "status", "received_on", "reference", "note"]
        widgets = {
            "received_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "amount": forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}),
            "reference": forms.TextInput(
                attrs={"placeholder": "Teller or transfer reference"}
            ),
            "note": forms.TextInput(attrs={"placeholder": "Optional"}),
        }

    def __init__(self, *args, applicant=None, **kwargs):
        self.applicant = applicant
        super().__init__(*args, **kwargs)
        for name in ("reference", "note"):
            self.fields[name].required = False
        if applicant is not None:
            due = applicant.admission_outstanding
            if due:
                self.fields["amount"].initial = due
                self.fields["amount"].help_text = (
                    f"Outstanding on this admission: {due:,.0f}."
                )
        self.fields["status"].help_text = (
            "Pending means the receipt is in but not yet checked. Only "
            "confirmed money counts towards the admission."
        )


class AdmissionsConfigForm(StyledFormMixin, forms.ModelForm):
    """The school's admissions policy -- the window, the nudge, and who sees it."""

    class Meta:
        model = AdmissionsConfig
        fields = [
            "enquiry_validity_days",
            "reminder_after_days",
            "bursar_can_view_applicants",
            "require_payment_before_enrolment",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["require_payment_before_enrolment"].label = (
            "Require the admission fee before enrolling"
        )


class PipelineFilterForm(StyledFormMixin, forms.Form):
    """The pipeline board's filters. Every field optional; blank means "all"."""

    q = forms.CharField(
        required=False,
        label="Search",
        widget=forms.TextInput(
            attrs={"placeholder": "Name, reference or parent", "autocomplete": "off"}
        ),
    )
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(), required=False, empty_label="All campuses"
    )
    level = forms.ChoiceField(
        required=False,
        choices=[("", "All levels")] + list(Level.choices),
        label="Level",
    )
    school_class = forms.ModelChoiceField(
        queryset=Class.objects.none(),
        required=False,
        empty_label="All classes",
        label="Class",
    )
    status = forms.ChoiceField(
        required=False,
        choices=[("", "All stages")] + list(ApplicantStatus.choices),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Scoped managers: a principal's filters only ever offer their own
        # campus, so the board cannot be widened by editing the query string.
        branches = Branch.objects.filter(is_active=True).order_by("name")
        self.fields["branch"].queryset = branches
        self.fields["school_class"].queryset = (
            Class.objects.filter(is_active=True).select_related("branch")
        )
        self.fields["school_class"].label_from_instance = (
            (lambda obj: f"{obj.branch.name} — {obj.display_name}")
            if len(branches) > 1
            else (lambda obj: obj.display_name)
        )
        if len(branches) < 2:
            # One campus is not a filter.
            del self.fields["branch"]
