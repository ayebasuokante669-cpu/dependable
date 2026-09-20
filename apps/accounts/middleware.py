"""Holds an account on a temporary password at Account settings until it picks its own."""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse


class RequirePasswordChangeMiddleware:
    """Send an account with ``must_change_password`` to Account settings.

    Every screen redirects there, the admin included, except the few that
    choosing a password needs: the settings page itself, the generator behind
    its Generate button, signing out, the reset-by-email flow, and static files.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and getattr(user, "must_change_password", False)
            and not self._exempt(request.path_info)
        ):
            # Say why the click went somewhere else. The settings page carries a
            # persistent notice about the state -- which is the right place for
            # something the user *must* act on -- but nothing there explains why
            # pressing "Students" landed them on Account settings.
            #
            # Safe to add here: MessageMiddleware is listed ahead of this one, so
            # `request._messages` exists. One message per redirect, and the
            # redirect renders the settings page immediately, which consumes it.
            messages.warning(
                request, "Choose your own password before carrying on."
            )
            return redirect("accounts:settings")
        return self.get_response(request)

    @staticmethod
    def _exempt(path: str) -> bool:
        allowed = (
            reverse("accounts:settings"),
            reverse("accounts:generate_password"),
            reverse("logout"),
            reverse("password_reset"),
            # /accounts/reset/<uid>/<token>/ and /accounts/reset/done/.
            reverse("password_reset_complete").removesuffix("done/"),
            settings.STATIC_URL,
            settings.MEDIA_URL,
        )
        return any(path.startswith(prefix) for prefix in allowed if prefix)
