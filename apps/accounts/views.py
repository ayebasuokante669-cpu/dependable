"""The password-generator endpoint behind the Generate button.

Generation lives on the server so there is exactly one implementation of the
policy. A JavaScript generator would be a second definition of "strong enough"
that no Django test could reach, and the two would drift the first time the
policy changed. Here the button and the form agree because they call the same
:func:`~apps.accounts.passwords.generate_password`, which in turn calls the same
validators the form does.

The endpoint has to be reachable signed-out -- signup is where it is needed
most -- so it is throttled. It is cheap in itself, but in production each call
runs the policy, and the policy includes a request to Have I Been Pwned; an
open, unthrottled endpoint would let anyone use this deployment to generate
traffic against theirs.
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .passwords import generate_password

logger = logging.getLogger(__name__)

#: Generous next to how often a human presses a button, low enough to be
#: useless for amplification.
RATE_LIMIT = 20
RATE_WINDOW_SECONDS = 60


def _client_ip(request) -> str:
    """Best-effort client identity for throttling.

    ``X-Forwarded-For`` is trusted because this only ever runs behind the
    platform's proxy, which sets it. Nothing security-critical hangs off the
    result: the worst case of a spoofed value is that a caller gets their own
    private bucket, which still costs them a request per token.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


@require_POST
def generate_password_view(request):
    """Return one generated password as JSON.

    POST rather than GET so it is CSRF-checked and never cached by anything in
    front of it -- a cached response here would hand the same password to
    everyone who asked.
    """
    key = f"pwgen:{_client_ip(request)}"
    # add() only succeeds on the first call in the window, which is what starts
    # the clock; incr() after that. The pair is not atomic across processes, so
    # a multi-worker deployment enforces this per worker -- fine for a limit
    # whose job is to bound amplification, not to be exact.
    cache.add(key, 0, RATE_WINDOW_SECONDS)
    try:
        used = cache.incr(key)
    except ValueError:
        # The key expired between add() and incr(). Treat as the first call.
        used = 1

    if used > RATE_LIMIT:
        logger.warning("Password generation rate limit hit for %s", key)
        return JsonResponse(
            {"error": "Too many requests. Wait a moment and try again."},
            status=429,
        )

    try:
        password = generate_password()
    except RuntimeError:
        # The policy has become unsatisfiable by the generator's shape. Log it
        # with the traceback and let the user type their own rather than
        # showing them a 500.
        logger.exception("Password generator could not satisfy the policy")
        return JsonResponse(
            {"error": "Could not generate a password. Please type one."},
            status=503,
        )

    return JsonResponse({"password": password})
