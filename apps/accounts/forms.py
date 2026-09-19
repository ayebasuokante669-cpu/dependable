"""The forms behind Account settings: a person's own email address and password."""

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError

from apps.core.forms import StyledFormMixin
from apps.core.roles import Role
from apps.schools.models import Branch

from .invites import set_random_password, unique_username


class EmailChangeForm(StyledFormMixin, forms.Form):
    """Change the address an account signs in with and recovers to.

    Asks for the current password: whoever holds an unattended session should
    not be able to point the account's password-reset emails at themselves.
    """

    email = forms.EmailField(
        label="New email address",
        help_text="You can sign in with it, and password-reset emails are sent to it.",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    current_password = forms.CharField(
        label="Current password",
        help_text="To confirm it is you.",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = BaseUserManager.normalize_email(self.cleaned_data["email"].strip())
        if email.lower() == (self.user.email or "").lower():
            raise ValidationError("That is already your email address.")
        others = get_user_model().objects.filter(email__iexact=email).exclude(pk=self.user.pk)
        if others.exists():
            raise ValidationError("Another account already uses that email address.")
        return email

    def clean_current_password(self):
        password = self.cleaned_data["current_password"]
        if not self.user.check_password(password):
            raise ValidationError("That is not your current password.")
        return password

    def save(self):
        self.user.email = self.cleaned_data["email"]
        self.user.save(update_fields=["email"])
        return self.user


class AccountPasswordForm(StyledFormMixin, PasswordChangeForm):
    """Django's password change on the design system.

    The old password, then the new one twice, checked against
    AUTH_PASSWORD_VALIDATORS -- the same policy signup and the reset flow apply.
    """

class StaffAccountForm(StyledFormMixin, forms.Form):
    """Create a login for somebody who works at this school.

    Two fields do different jobs and are deliberately not one: **permission
    role** is what the account may see, **job title** is what the person does.
    Promoting a bursar to "Head of Finance" is an HR change, not an access
    change, which is why the platform refuses to infer either from the other.

    No password field. The account is given a long random password nobody --
    including the proprietor creating it -- ever sees, and the person sets
    their own from an emailed link. A proprietor who typed a colleague's
    password would know it, and the colleague could never be sure they didn't.
    """

    #: Roles a school may hand out. PLATFORM_OWNER is absent on purpose and is
    #: validated for below as well: platform staff are made with the
    #: create_platform_owner command, by somebody with a shell, never from a
    #: tenant's own screen.
    ASSIGNABLE_ROLES = (Role.SCHOOL_OWNER, Role.PRINCIPAL, Role.BURSAR)

    full_name = forms.CharField(
        label="Full name", max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    email = forms.EmailField(
        label="Email address",
        help_text="They sign in with it, and the link to set their password "
                  "goes here.",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    role = forms.ChoiceField(
        label="Permission role",
        help_text="What the account may see and change.",
    )
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        required=False,
        label="Campus",
        help_text="Principals and bursars work at one campus. Leave blank for "
                  "an account that covers the whole school.",
    )
    job_title = forms.CharField(
        label="Job title", max_length=120, required=False,
        help_text="What they do, e.g. 'Head of Mathematics'. No effect on access.",
    )
    phone = forms.CharField(label="Phone", max_length=32, required=False)

    def __init__(self, *args, creator=None, **kwargs):
        self.creator = creator
        self.school = getattr(creator, "school", None)
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = [
            (role.value, role.label)
            for role in Role
            if role in self.ASSIGNABLE_ROLES
        ]
        # Scoped manager: the campuses offered are this school's own, so the
        # dropdown cannot name -- or be edited to name -- another school's.
        self.fields["branch"].queryset = Branch.objects.filter(is_active=True)

    def clean_email(self):
        email = BaseUserManager.normalize_email(self.cleaned_data["email"].strip())
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise ValidationError(
                "An account with that email address already exists. If they "
                "have forgotten their password, they can reset it from the "
                "sign-in page."
            )
        return email

    def clean_role(self):
        role = self.cleaned_data["role"]
        if role not in {r.value for r in self.ASSIGNABLE_ROLES}:
            # Unreachable from the form, reachable by hand.
            raise ValidationError("That is not a role this school can assign.")
        return role

    def clean(self):
        cleaned = super().clean()
        if self.school is None:
            raise ValidationError(
                "This account is not attached to a school, so it cannot create "
                "staff for one."
            )
        role, branch = cleaned.get("role"), cleaned.get("branch")
        if role in {Role.PRINCIPAL, Role.BURSAR} and branch is None:
            self.add_error(
                "branch",
                f"A {Role(role).label.lower()} works at one campus. Choose which.",
            )
        if role == Role.SCHOOL_OWNER and branch is not None:
            # Not an error worth refusing over: an owner sees every campus
            # whatever is stored, so the tidier answer is to store nothing.
            cleaned["branch"] = None
        if branch is not None and branch.school_id != self.school.pk:
            raise ValidationError("That campus belongs to a different school.")
        return cleaned

    def save(self):
        """Create the account with a password nobody knows. Unsaved until now."""
        first, _, last = " ".join(self.cleaned_data["full_name"].split()).partition(" ")
        user = get_user_model()(
            username=unique_username(self.cleaned_data["email"]),
            email=self.cleaned_data["email"],
            first_name=first[:150],
            last_name=last[:150],
            role=self.cleaned_data["role"],
            # Never from the form: the school is whoever is signed in, so a
            # posted school_id cannot file an account under another tenant.
            school=self.school,
            branch=self.cleaned_data.get("branch"),
            job_title=self.cleaned_data.get("job_title", ""),
            phone=self.cleaned_data.get("phone", ""),
        )
        set_random_password(user)
        user.full_clean(exclude=["password"])
        user.save()
        return user

