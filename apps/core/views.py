"""The shell's own views.

Feature screens live in their own apps.  What exists here is the landing page
that proves the layout, the navigation and the tenant context are wired up.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.schools.models import Branch, School

from .roles import Role, scope_for


@login_required
def dashboard(request):
    """Role-aware placeholder that each role dashboard will later replace."""
    user = request.user
    role = getattr(user, "role", None)

    # These counts go through the scoped managers, so what a school owner sees
    # here is already limited to their own school without any filtering below.
    context = {
        "page_title": "Dashboard",
        "role_label": Role(role).label if role in Role.values else "No role",
        "scope": scope_for(role).value,
        "school_count": School.objects.count(),
        "branch_count": Branch.objects.count(),
        "branches": Branch.objects.select_related("school", "head")[:10],
    }
    return render(request, "core/dashboard.html", context)
