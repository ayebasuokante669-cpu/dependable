"""Create a school, a branch and one user per role -- enough to click around.

    python manage.py bootstrap_tenant --name "Demo School"

Everything this creates is obviously fake on purpose. The default school is
"Demo School" rather than a plausible one, because a scaffold that looks like a
customer is a scaffold somebody eventually mistakes for a customer -- and the
genuine pilot tenant lives in the seed commands, not here.

Every account is created with the same password (``--password``, default
``demo-password``), which is fine for a local scaffold and nowhere else.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from apps.core.roles import Role
from apps.schools.models import Branch, Plan, School, SchoolStatus

User = get_user_model()

#: Django model permissions granted per role, so each demo account can reach
#: the admin and see the tenant scoping in action.  Row-level isolation still
#: comes from TenantScopedAdminMixin -- these only open the door.
ROLE_PERMISSIONS: dict[str, list[tuple[str, str, list[str]]]] = {
    Role.SCHOOL_OWNER: [
        ("schools", "school", ["view", "change"]),
        ("schools", "branch", ["view", "add", "change", "delete"]),
        ("accounts", "user", ["view", "add", "change"]),
    ],
    Role.PRINCIPAL: [
        ("schools", "school", ["view"]),
        ("schools", "branch", ["view", "change"]),
        ("accounts", "user", ["view", "add", "change"]),
    ],
    Role.BURSAR: [
        ("schools", "school", ["view"]),
        ("schools", "branch", ["view"]),
        ("accounts", "user", ["view"]),
    ],
}


def permissions_for(role: str):
    spec = ROLE_PERMISSIONS.get(role)
    if not spec:
        return Permission.objects.none()
    query = Q(pk__in=[])
    for app_label, model, actions in spec:
        query |= Q(
            content_type__app_label=app_label,
            codename__in=[f"{action}_{model}" for action in actions],
        )
    return Permission.objects.filter(query)


class Command(BaseCommand):
    help = "Create a demo school with a branch and one user per permission role."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="Demo School")
        parser.add_argument("--branch", default="Main Campus")
        parser.add_argument("--city", default="Lagos")
        parser.add_argument("--state", default="Lagos")
        parser.add_argument("--password", default="demo-password")

    @transaction.atomic
    def handle(self, *args, **options):
        password = options["password"]

        school, created = School.all_objects.get_or_create(
            name=options["name"],
            defaults={"plan": Plan.GROWTH, "status": SchoolStatus.ACTIVE},
        )
        self.stdout.write(
            f"{'Created' if created else 'Reusing'} school: {school.name}"
        )

        branch, _ = Branch.all_objects.get_or_create(
            school=school,
            name=options["branch"],
            defaults={"city": options["city"], "state": options["state"]},
        )

        slug = school.slug
        accounts = [
            ("platform.owner", Role.PLATFORM_OWNER, None, None, "Platform Operations"),
            (f"{slug}.owner", Role.SCHOOL_OWNER, school, None, "Proprietor"),
            (f"{slug}.principal", Role.PRINCIPAL, school, branch, "Principal"),
            (f"{slug}.bursar", Role.BURSAR, school, branch, "Finance Officer"),
        ]

        for username, role, user_school, user_branch, job_title in accounts:
            # An address per account, so the password-reset flow has somewhere
            # to send to. Nothing is delivered in dev -- the console backend
            # prints the link -- but a user with no email cannot be reset at all,
            # and "nothing happened" is the least debuggable failure there is.
            email = f"{username}@example.com"
            fields = {
                "school": user_school,
                "branch": user_branch,
                "role": role,
                "job_title": job_title,
                "email": email,
                "first_name": job_title.split()[0],
                "last_name": school.name.split()[0],
                # Admin access so every role can exercise the tenant-scoped
                # admin; real deployments should not do this by default.
                "is_staff": True,
                "is_superuser": role == Role.PLATFORM_OWNER,
            }
            user, made = User.objects.get_or_create(
                username=username, defaults=fields
            )
            if made:
                user.set_password(password)
                user.save()
            else:
                # Re-running after this file changes should bring an existing
                # demo account up to date rather than quietly leaving it behind.
                changed = [f for f, v in fields.items() if getattr(user, f, None) != v]
                if changed:
                    for field, value in fields.items():
                        setattr(user, field, value)
                    user.save(update_fields=list(fields))
            if not user.is_superuser:
                user.user_permissions.set(permissions_for(role))
            self.stdout.write(f"  {'+' if made else '='} {username}  ({role})  {email}")

        if branch.head_id is None:
            branch.head = User.objects.filter(
                school=school, role=Role.PRINCIPAL
            ).first()
            branch.save(update_fields=["head"])

        self.stdout.write(
            self.style.SUCCESS(f"\nDone. Sign in with any username above / {password!r}.")
        )
