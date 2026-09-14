"""The forms behind Account settings: a person's own email address and password."""

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError

from apps.core.forms import StyledFormMixin


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
