"""Server-side navigation -- the equivalent of a client ``navFor(role)``.

Navigation is derived from the permission role, not from the job title, and is
resolved on the server so a browser can never reveal a link the account is not
allowed to follow.  Destinations that do not exist yet resolve to a disabled
entry, which keeps the shell honest while feature screens are still being
built.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.urls import NoReverseMatch, reverse

from .roles import Role
from .tenancy import TenantContext

#: Sentinel url_name: "wherever this role's dashboard is". Resolved per role in
#: :func:`_resolve`, so the sidebar's Dashboard entry points a bursar at the
#: bursar dashboard and a principal at their branch's.
HOME = "__role_home__"

#: The one place that answers "where does this role live?". Both the sidebar
#: entry above and the post-login redirect read it, so the link in the shell and
#: the page you land on after signing in can never drift apart.
ROLE_HOME: dict[str, str] = {
    Role.PLATFORM_OWNER: "core:platform_overview",
    Role.SCHOOL_OWNER: "core:school_dashboard",
    Role.PRINCIPAL: "core:branch_dashboard",
    Role.BURSAR: "core:bursar_dashboard",
}

#: An account whose role we do not recognise gets the narrowest dashboard, the
#: same way scope_for() gives it the narrowest visibility.
DEFAULT_HOME = "core:branch_dashboard"


def home_url_name(role: str | None) -> str:
    """The URL name of ``role``'s dashboard."""
    return ROLE_HOME.get(role, DEFAULT_HOME)


def home_url_for(user) -> str:
    """Where ``user`` should land after signing in.

    Goes through :class:`TenantContext` rather than reading ``user.role``
    directly, so a Django superuser -- who is promoted to platform scope for
    every other purpose -- is sent to the platform overview rather than to
    whatever role happens to be stored on their row.
    """
    return reverse(home_url_name(TenantContext.from_user(user).role))

#: Heroicons-style outline paths, drawn on a 24x24 viewbox.
ICONS: dict[str, list[str]] = {
    "home": [
        "m2.25 12 8.954-8.955c.44-.439 1.152-.439 1.591 0L21.75 12M4.5 9.75v10.125"
        "c0 .621.504 1.125 1.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125h2.25"
        "c.621 0 1.125.504 1.125 1.125V21h4.125c.621 0 1.125-.504 1.125-1.125V9.75"
        "M8.25 21h8.25",
    ],
    "building": [
        "M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5"
        "m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75"
        "c.621 0 1.125.504 1.125 1.125V21",
    ],
    "branches": [
        "M6.429 9.75 2.25 12l4.179 2.25m0-4.5 5.571 3 5.571-3m-11.142 0L2.25 7.5"
        "L12 2.25l9.75 5.25-4.179 2.25m0 0L21.75 12l-4.179 2.25m0 0 4.179 2.25L12 21.75"
        "L2.25 16.5l4.179-2.25m11.142 0-5.571 3-5.571-3",
    ],
    "users": [
        "M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125"
        " 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106"
        "A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109"
        "a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375"
        " 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z",
    ],
    "students": [
        "M4.26 10.147a60.438 60.438 0 0 0-.491 6.347A48.62 48.62 0 0 1 12 20.904"
        "a48.62 48.62 0 0 1 8.232-4.41 60.46 60.46 0 0 0-.491-6.347m-15.482 0"
        "a50.636 50.636 0 0 0-2.658-.813A59.906 59.906 0 0 1 12 3.493a59.903 59.903"
        " 0 0 1 10.399 5.84c-.896.248-1.783.52-2.658.814m-15.482 0A50.717 50.717 0 0 1"
        " 12 13.489a50.702 50.702 0 0 1 7.74-3.342M6.75 15a.75.75 0 1 0 0-1.5.75.75"
        " 0 0 0 0 1.5Zm0 0v-3.675A55.378 55.378 0 0 1 12 8.443m-7.007 11.55A5.981"
        " 5.981 0 0 0 6.75 15.75v-1.5",
    ],
    "money": [
        "M2.25 8.25h19.5M2.25 9h19.5m-16.5 5.25h6m-6 2.25h3m-3.75 3h15a2.25 2.25 0 0 0"
        " 2.25-2.25V6.75A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25v10.5"
        "A2.25 2.25 0 0 0 4.5 19.5Z",
    ],
    "grid": [
        "M3.75 6A2.25 2.25 0 0 1 6 3.75h2.25A2.25 2.25 0 0 1 10.5 6v2.25a2.25 2.25 0 0 1"
        "-2.25 2.25H6a2.25 2.25 0 0 1-2.25-2.25V6ZM3.75 15.75A2.25 2.25 0 0 1 6 13.5h2.25"
        "a2.25 2.25 0 0 1 2.25 2.25V18a2.25 2.25 0 0 1-2.25 2.25H6A2.25 2.25 0 0 1 3.75 18"
        "v-2.25ZM13.5 6a2.25 2.25 0 0 1 2.25-2.25H18A2.25 2.25 0 0 1 20.25 6v2.25"
        "A2.25 2.25 0 0 1 18 10.5h-2.25a2.25 2.25 0 0 1-2.25-2.25V6ZM13.5 15.75"
        "a2.25 2.25 0 0 1 2.25-2.25H18a2.25 2.25 0 0 1 2.25 2.25V18A2.25 2.25 0 0 1 18 20.25"
        "h-2.25A2.25 2.25 0 0 1 13.5 18v-2.25Z",
    ],
    "calendar": [
        "M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5"
        "A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5"
        "A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5"
        "A2.25 2.25 0 0 1 21 11.25v7.5",
    ],
    "book": [
        "M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987"
        " 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292"
        "c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292"
        "m0-14.25v14.25",
    ],
    "chart": [
        "M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75"
        "C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75Z"
        "M9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125"
        "v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625Z"
        "M16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75"
        "c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z",
    ],
    "chat": [
        "M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.375 0"
        "a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0"
        " .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764"
        " 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065"
        " 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189"
        " 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z",
    ],
    "cog": [
        "M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281"
        "c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456"
        "a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827"
        "c-.293.24-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991"
        "l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491"
        "l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128"
        "c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594"
        "c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87"
        "a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456"
        "a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827"
        "c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992"
        "l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491"
        "l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128"
        ".332-.183.582-.495.644-.869l.214-1.28Z",
        "M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
    ],
}


@dataclass(frozen=True)
class NavItem:
    label: str
    url_name: str
    icon: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class NavSection:
    label: str
    items: tuple[NavItem, ...]


@dataclass(frozen=True)
class ResolvedItem:
    """A nav entry with its href already worked out for the template."""

    label: str
    href: str
    icon_paths: list[str] = field(default_factory=list)
    available: bool = True
    active: bool = False


@dataclass(frozen=True)
class ResolvedSection:
    label: str
    items: list[ResolvedItem]


ALL_ROLES = tuple(Role.values)
_LEADERSHIP = (Role.PLATFORM_OWNER, Role.SCHOOL_OWNER, Role.PRINCIPAL)

#: The whole navigation map, declared once.  Feature destinations are listed
#: now and light up on their own as the URL names come into existence.
NAVIGATION: tuple[NavSection, ...] = (
    NavSection(
        label="Overview",
        items=(
            # Resolves per role -- see HOME and ROLE_HOME above.
            NavItem("Dashboard", HOME, "home", ALL_ROLES),
            NavItem("Reports", "reports:index", "chart", _LEADERSHIP + (Role.BURSAR,)),
        ),
    ),
    NavSection(
        label="Platform",
        items=(
            NavItem("Schools", "admin:schools_school_changelist", "building",
                    (Role.PLATFORM_OWNER,)),
            NavItem("Plans & Billing", "billing:index", "money", (Role.PLATFORM_OWNER,)),
        ),
    ),
    NavSection(
        label="School",
        items=(
            NavItem("Branches", "admin:schools_branch_changelist", "branches",
                    (Role.PLATFORM_OWNER, Role.SCHOOL_OWNER)),
            NavItem("Staff", "admin:accounts_user_changelist", "users", _LEADERSHIP),
            # A bursar reads the roster to record payments against it;
            # owner/principal level enrols and edits (Capability.MANAGE_STUDENTS),
            # so every role gets the link.
            NavItem("Students", "students:student_list", "students", ALL_ROLES),
        ),
    ),
    NavSection(
        label="Academics",
        items=(
            # Every role may read the academic setup; only owner/principal
            # level roles get the edit controls (see Capability.MANAGE_ACADEMICS).
            NavItem("Classes", "academics:class_list", "grid", ALL_ROLES),
            NavItem("Subjects", "academics:subject_list", "book", ALL_ROLES),
        ),
    ),
    NavSection(
        label="Finance",
        items=(
            # A bursar reads the fee structure; owner/principal level sets it
            # (see Capability.MANAGE_FEES), so every role gets the link.
            NavItem("Fee Structures", "fees:structure_list", "money", ALL_ROLES),
            NavItem("Terms", "admin:fees_term_changelist", "calendar", _LEADERSHIP),
            # A bursar records and confirms; owner and principal level also
            # view and void (see Capability.VOID_PAYMENTS), so every role gets
            # the link.
            NavItem("Payments", "payments:index", "money", ALL_ROLES),
            NavItem("Outstanding", "payments:outstanding", "chart", ALL_ROLES),
        ),
    ),
    NavSection(
        label="Communication",
        items=(
            NavItem("Messaging", "messaging:index", "chat", ALL_ROLES),
        ),
    ),
    NavSection(
        label="Settings",
        items=(
            NavItem("Settings", "settings:index", "cog", _LEADERSHIP),
        ),
    ),
)


def _resolve(item: NavItem, current_path: str, role: str | None = None) -> ResolvedItem:
    url_name = home_url_name(role) if item.url_name == HOME else item.url_name
    try:
        href = reverse(url_name)
        available = True
    except NoReverseMatch:
        # Destination not built yet -- render it greyed out rather than 404ing.
        href = "#"
        available = False
    active = available and href != "/" and current_path.startswith(href)
    if available and href == "/":
        active = current_path == "/"
    return ResolvedItem(
        label=item.label,
        href=href,
        icon_paths=ICONS.get(item.icon, []),
        available=available,
        active=active,
    )


def nav_for(role: str | None, current_path: str = "") -> list[ResolvedSection]:
    """Return the navigation tree a holder of ``role`` should see.

    Sections with no visible items are dropped, so a bursar simply never
    receives a "Platform" heading.
    """
    if not role:
        return []
    sections: list[ResolvedSection] = []
    for section in NAVIGATION:
        items = [
            _resolve(i, current_path, role) for i in section.items if role in i.roles
        ]
        if items:
            sections.append(ResolvedSection(label=section.label, items=items))
    return sections
