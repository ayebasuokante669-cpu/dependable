"""Form helpers that keep hand-written forms on the design system, plus the
one form that creates a tenant from nothing.
"""

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import slugify

from apps.schools.models import Branch, School


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


class SchoolSignupForm(StyledFormMixin, forms.Form):
    """Onboarding step 1: a school, its first campus, and its owner.

    The only form on the platform that runs with no tenant context at all --
    there is no tenant yet. Every queryset here therefore goes through
    ``all_objects``: the scoped managers would return nothing to an anonymous
    visitor, and a uniqueness check that cannot see the existing rows is not a
    uniqueness check.
    """

    #: What the first campus is called until the school renames it. Schools with
    #: one site never think about branches; schools with several rename this and
    #: add the others under Branches.
    DEFAULT_BRANCH_NAME = "Main Campus"

    school_name = forms.CharField(
        label="School name",
        max_length=200,
        widget=forms.TextInput(
            attrs={"placeholder": "Bright Future Academy", "autofocus": True}
        ),
    )
    full_name = forms.CharField(
        label="Your name",
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "Your full name"}),
        help_text="The proprietor or whoever will own the account.",
    )
    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(
            attrs={"placeholder": "you@school.com", "autocomplete": "email"}
        ),
        help_text="You will sign in with this.",
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password_confirm = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean_school_name(self):
        return " ".join(self.cleaned_data["school_name"].split())

    def clean_full_name(self):
        return " ".join(self.cleaned_data["full_name"].split())

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        User = get_user_model()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(
                "An account with that email already exists. Sign in instead, or "
                "use the password reset link if you have forgotten it."
            )
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        # Run Django's configured validators here rather than at save time, so
        # a weak password is a field error on the form the user is looking at.
        validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        confirm = cleaned.get("password_confirm")
        if password and confirm and password != confirm:
            self.add_error("password_confirm", "The two passwords do not match.")
        return cleaned

    # -- names ---------------------------------------------------------------

    @property
    def first_name(self) -> str:
        return self.cleaned_data["full_name"].split(" ")[0][:150]

    @property
    def last_name(self) -> str:
        """Everything after the first word. One-word names leave this blank."""
        parts = self.cleaned_data["full_name"].split(" ")
        return " ".join(parts[1:])[:150]

    def username(self) -> str:
        """A unique username derived from the email.

        The model still carries a username because ``AbstractUser`` does, but
        nothing asks the user for one -- they sign in with the email they typed.
        Collisions are resolved with a counter rather than by rejecting the
        signup, because two people at different schools sharing a local part is
        their business, not an error.
        """
        User = get_user_model()
        base = slugify(self.cleaned_data["email"].split("@")[0]) or "owner"
        base = base[:140]
        candidate, counter = base, 2
        while User.objects.filter(username__iexact=candidate).exists():
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    # -- creation ------------------------------------------------------------

    @transaction.atomic
    def save(self):
        """Create the school, its first branch and the owner, or nothing at all.

        One transaction: a signup that produced a School with no owner would
        leave a tenant nobody can sign in to, and no screen from which to fix it.
        """
        from .roles import Role

        User = get_user_model()

        school = School.all_objects.create(
            name=self.cleaned_data["school_name"],
            contact_email=self.cleaned_data["email"],
        )
        branch = Branch.all_objects.create(
            school=school, name=self.DEFAULT_BRANCH_NAME
        )
        owner = User.objects.create_user(
            username=self.username(),
            email=self.cleaned_data["email"],
            password=self.cleaned_data["password"],
            first_name=self.first_name,
            last_name=self.last_name,
            role=Role.SCHOOL_OWNER,
            school=school,
            # Deliberately no branch: an owner sees every campus, and pinning
            # them to the first one would narrow them the day they open a second.
            branch=None,
            job_title="Proprietor",
        )
        return school, branch, owner


class SchoolProfileForm(StyledFormMixin, forms.ModelForm):
    """A school's own identity: what it is called, and what it looks like.

    The logo is the only genuinely new field here, and it is optional on
    purpose -- most schools will never upload one, and the initial-in-a-square
    fallback is a real answer rather than a gap. Clearing the checkbox removes
    the file, which is the only way a school can undo a bad upload without
    asking us.
    """

    class Meta:
        model = School
        fields = ["name", "logo", "contact_email", "contact_phone"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Bright Future Academy"}),
            "contact_email": forms.EmailInput(
                attrs={"placeholder": "office@school.com", "autocomplete": "email"}
            ),
            "contact_phone": forms.TextInput(
                attrs={"placeholder": "0803 123 4567", "inputmode": "tel"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].label = "School name"
        self.fields["logo"].label = "School logo"
        self.fields["contact_email"].label = "Office email"
        self.fields["contact_phone"].label = "Office phone"

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())
