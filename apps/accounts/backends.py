"""Sign in with an email address or a username.

Nobody types a username at signup -- it is derived from the email -- and it does
not change when the email later does. Logging in by username alone would leave
someone who corrected their email on Account settings signing in with a name
built from the old one. Matching on email as well is what decouples the two.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameBackend(ModelBackend):
    """Django's ``ModelBackend``, also accepting the account's email address.

    The username is tried first, exactly as before, so every existing sign-in
    keeps working. An email matches case-insensitively, and only when exactly
    one account has it: email is not unique at the database level, and a login
    that picked one of two accounts would be signing someone in as a stranger.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if username is None or password is None:
            return None
        identifier = username.strip()

        user = User._default_manager.filter(**{User.USERNAME_FIELD: identifier}).first()
        if user is None and "@" in identifier:
            matches = list(User._default_manager.filter(email__iexact=identifier)[:2])
            user = matches[0] if len(matches) == 1 else None

        if user is None:
            # Hash anyway, so "no such account" takes as long as a wrong
            # password and the response time does not say which it was.
            User().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
