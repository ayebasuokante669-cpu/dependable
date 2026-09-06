"""Recording, confirming and voiding money.

The student picker is a text input backed by a native ``<datalist>`` rather
than a long ``<select>`` or a JavaScript autocomplete. Three reasons, in order
of how much they matter:

* it is how the job is actually done -- the bursar has a receipt in front of
  them with an admission number on it, and types it;
* the browser's own search matches on the whole option, so typing a surname
  finds the child too;
* it needs no endpoint and no script, so it works on the front-desk machine
  with whatever is installed on it.

The typed value is resolved back to a ``Student`` through the tenant-scoped
manager, so a copied-and-pasted admission number from another school finds
nothing.
"""

from __future__ import annotations

import re
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.forms import StyledFormMixin
from apps.fees.models import Term
from apps.students.models import Student, StudentStatus

from .models import Payment, PaymentLabel, PaymentMethod, PaymentStatus

#: What an uploaded receipt may be, and how big. A parent's bank slip is a
#: phone photo or a PDF; anything else is a mistake worth catching at the form
#: rather than at the storage layer.
RECEIPT_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf"}
RECEIPT_MAX_BYTES = 8 * 1024 * 1024

#: "FA/2025/001 — Chinaza Okonkwo (JSS 1A)" → the admission number.
_PICKER = re.compile(r"^\s*([^—]+?)\s*(?:—.*)?$")


def picker_label(student: Student) -> str:
    """One datalist option. The admission number leads, because that is what is
    printed on the receipt the bursar is holding."""
    return (
        f"{student.admission_number} — {student.full_name} "
        f"({student.school_class.display_name})"
    )


class StudentPickerField(forms.CharField):
    """A student, chosen by typing. Cleans to the ``Student`` instance."""

    widget = forms.TextInput

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("label", "Student")
        kwargs.setdefault(
            "help_text",
            "Type an admission number or a name and pick from the list.",
        )
        super().__init__(*args, **kwargs)
        self.widget.attrs.setdefault("list", "student-options")
        self.widget.attrs.setdefault("autocomplete", "off")
        self.widget.attrs.setdefault("placeholder", "FA/2025/001")

    def clean(self, value):
        value = super().clean(value)
        if not value:
            if self.required:
                raise ValidationError("Choose the student this payment is for.")
            return None

        match = _PICKER.match(value)
        admission_number = (match.group(1) if match else value).strip()

        student = Student.objects.filter(
            admission_number__iexact=admission_number
        ).first()
        if student is None:
            raise ValidationError(
                f'No student here has the admission number "{admission_number}". '
                f"Pick one from the list as you type."
            )
        return student

    def prepare_value(self, value):
        """Render an existing choice the same way the datalist offers it."""
        if isinstance(value, Student):
            return picker_label(value)
        if value and str(value).isdigit():
            student = Student.objects.filter(pk=value).first()
            if student is not None:
                return picker_label(student)
        return value


def picker_options() -> list[str]:
    """Every student the caller may record a payment against.

    Active only: money is taken against a child who is here. Tenant-scoped, so
    the list is the caller's own campus.
    """
    return [
        picker_label(student)
        for student in Student.objects.filter(
            status=StudentStatus.ACTIVE
        ).select_related("school_class")
    ]


class PaymentForm(StyledFormMixin, forms.ModelForm):
    """Record a payment, or correct a pending one before confirming it."""

    student = StudentPickerField()

    class Meta:
        model = Payment
        fields = [
            "student",
            "amount",
            "date_paid",
            "label",
            "method",
            "reference",
            "receipt",
            "note",
        ]
        widgets = {
            "amount": forms.NumberInput(
                attrs={"step": "0.01", "min": "0.01", "inputmode": "decimal",
                       "placeholder": "50000"}
            ),
            "date_paid": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "reference": forms.TextInput(
                attrs={"placeholder": "Teller / transfer ref", "autocomplete": "off"}
            ),
            "note": forms.TextInput(
                attrs={"placeholder": "Anything the receipt does not say"}
            ),
            "receipt": forms.ClearableFileInput(
                attrs={"accept": "image/*,application/pdf"}
            ),
        }

    #: Rendered as two buttons rather than a status dropdown -- "Save as
    #: pending" and "Record as confirmed" are two different intentions, and a
    #: select makes the bursar read three words to express one of them.
    confirm_now = forms.BooleanField(
        required=False,
        initial=True,
        label="Confirm this payment now",
        help_text="Leave unticked to hold it in the pending queue for checking.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date_paid"].initial = timezone.localdate()
        self.fields["amount"].label = "Amount (₦)"
        self.fields["reference"].label = "Reference"
        self.fields["note"].label = "Note"
        self.student_options = picker_options()

        if self.instance.pk:
            self.fields["confirm_now"].initial = (
                self.instance.status == PaymentStatus.CONFIRMED
            )

    def clean_amount(self) -> Decimal:
        amount = self.cleaned_data["amount"]
        if amount is not None and amount <= 0:
            raise ValidationError("A payment has to be more than nothing.")
        return amount

    def clean_receipt(self):
        upload = self.cleaned_data.get("receipt")
        # An unchanged existing file comes back as the FieldFile, which has
        # nothing to re-validate.
        if not upload or not hasattr(upload, "content_type"):
            return upload
        name = (upload.name or "").lower()
        if not any(name.endswith(ext) for ext in RECEIPT_TYPES):
            raise ValidationError(
                "Upload the receipt as an image or a PDF."
            )
        if upload.size > RECEIPT_MAX_BYTES:
            raise ValidationError(
                f"That file is {upload.size // 1024 // 1024} MB. Keep receipts "
                f"under {RECEIPT_MAX_BYTES // 1024 // 1024} MB."
            )
        return upload

    def clean(self):
        cleaned = super().clean()
        student = cleaned.get("student")
        if student is None:
            return cleaned

        # Set before the model's own clean() runs, so its branch check has
        # something to compare against.
        self.instance.student = student
        self.instance.branch = student.branch
        self.instance.school_id = student.school_id

        term = self.resolve_term(student)
        if term is None:
            self.add_error(
                None,
                f"{student.branch.name} has no current term, so there is nothing "
                f"to credit this payment to. Mark a term as current first.",
            )
        else:
            self.instance.term = term
        return cleaned

    def resolve_term(self, student) -> Term | None:
        """The term a payment lands in: the student's branch's current one.

        Kept on the row rather than worked out from the date every time a
        balance is read. A payment made in the holidays for next term is still
        that term's money, and only the person recording it knows which.
        """
        if self.instance.pk and self.instance.term_id:
            return self.instance.term
        return Term.objects.filter(
            branch_id=student.branch_id, is_current=True
        ).first()

    def save(self, commit=True, *, user=None):
        payment = super().save(commit=False)
        confirm = self.cleaned_data.get("confirm_now")

        if payment.pk is None:
            payment.recorded_by = user
        if confirm:
            payment.confirm(by=user, save=False)
        else:
            payment.status = PaymentStatus.PENDING
            payment.confirmed_by = None
            payment.confirmed_at = None

        if commit:
            payment.save()
        return payment


class PendingConfirmForm(PaymentForm):
    """The pending queue's confirm step.

    Deliberately the same form: the client's flow is "someone uploads a
    receipt, the bursar confirms who and what it is for", which means the
    bursar must be able to *correct* the student, label and amount at the
    moment of confirming. A separate confirm-only form would force them to
    edit, go back, and confirm.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["confirm_now"].initial = True
        self.fields["confirm_now"].label = "Confirm this payment"
        self.fields["confirm_now"].help_text = (
            "Untick to leave it pending and come back to it."
        )


class VoidForm(StyledFormMixin, forms.Form):
    """Reversing a confirmed payment.

    A reason is required, not optional. Voiding moves a real balance, and "why
    is this 50,000 gone?" is asked months later by someone who was not there.
    """

    reason = forms.CharField(
        label="Why is this being voided?",
        max_length=250,
        widget=forms.TextInput(
            attrs={"placeholder": "Duplicate entry / wrong student / cheque bounced"}
        ),
    )
    confirm = forms.BooleanField(
        label="I understand this changes the student's balance",
        error_messages={"required": "Tick the box to confirm the void."},
    )


class PaymentFilterForm(forms.Form):
    """The branch-wide list's filter bar. Nothing here is saved."""

    q = forms.CharField(
        required=False,
        label="Search",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Student, admission no. or reference",
                "type": "search",
                "autocomplete": "off",
            }
        ),
    )
    status = forms.ChoiceField(
        required=False,
        label="Status",
        choices=[("", "All statuses")] + list(PaymentStatus.choices),
    )
    label = forms.ChoiceField(
        required=False,
        label="For",
        choices=[("", "All labels")] + list(PaymentLabel.choices),
    )
    method = forms.ChoiceField(
        required=False,
        label="Method",
        choices=[("", "All methods")] + list(PaymentMethod.choices),
    )
    since = forms.DateField(
        required=False,
        label="From",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    until = forms.DateField(
        required=False,
        label="To",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "field-input")

    def clean(self):
        cleaned = super().clean()
        since, until = cleaned.get("since"), cleaned.get("until")
        if since and until and since > until:
            self.add_error("until", "The end date is before the start date.")
        return cleaned
