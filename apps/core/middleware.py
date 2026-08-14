"""Installs the tenant context for the life of each request."""

from __future__ import annotations

from .tenancy import TenantContext, activate, deactivate


class TenantMiddleware:
    """Derive the tenant from ``request.user`` and publish it to the ORM.

    Must sit *after* ``AuthenticationMiddleware`` so ``request.user`` exists.
    The context is always reset in a ``finally`` block: worker threads are
    reused, and a leaked context would hand the next request someone else's
    school.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        context = TenantContext.from_user(getattr(request, "user", None))
        request.tenant = context
        token = activate(context)
        try:
            return self.get_response(request)
        finally:
            deactivate(token)
