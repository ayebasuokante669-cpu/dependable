"""Per-request plumbing: who is asking, and which features their school has."""

from __future__ import annotations

from django.shortcuts import render

from .tenancy import TenantContext, activate, deactivate


def module_unavailable(request, module):
    """The answer to a URL belonging to a module this school does not have.

    A 403 with a page that says which feature it was and who can switch it on --
    not a 404, which would be a small lie to somebody who works here, and not a
    redirect to the dashboard, which reads as the click having failed. The status
    stays 403 so a crawler or a script sees a refusal rather than a page.
    """
    from .branding import SUPPORT_EMAIL

    return render(
        request,
        "403_module.html",
        {
            "module": module,
            "support_email": SUPPORT_EMAIL,
            "page_title": f"{module.label} is not available",
        },
        status=403,
    )


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


class ModuleAccessMiddleware:
    """Refuse a URL that belongs to a module this school does not have.

    A middleware rather than a view mixin, and deliberately. The requirement is
    that a switched-off module is *never* reachable by typing its URL, and a mixin
    delivers that only for as long as everybody remembers to add it -- the next
    screen under ``/admissions/`` would be a hole nobody noticed. Matching on the
    URL prefix instead means a module's routes are covered before they are written.

    What it does *not* gate:

    * anything no module claims -- the dashboards, the roster, account settings;
    * an always-on module, which cannot be switched off, so there is nothing to
      check and no query worth making;
    * an unauthenticated request, because every prefix a module owns is behind a
      login and ``LoginRequiredMixin`` gives a better answer than this would;
    * a school's *public* enquiry page, which is not under a module prefix. It
      belongs to the school in the URL rather than to the reader, so it checks for
      itself -- see ``apps.admissions.views.PublicEnquiryView``.

    Sits after ``RequirePasswordChangeMiddleware`` so an account on a temporary
    password is sent to choose one before being told a module is unavailable: the
    password is the more fundamental gate, and answering with the module first
    would be a dead end for somebody who cannot use any screen yet.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        refusal = self._refuse(request)
        return refusal if refusal is not None else self.get_response(request)

    def _refuse(self, request):
        from .modules import enabled_for_user, module_for_path

        module = module_for_path(request.path)
        if module is None or module.always_on:
            return None

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        if module.key in enabled_for_user(user):
            return None
        return module_unavailable(request, module)
