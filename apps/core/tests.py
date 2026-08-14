"""Tests for the row-level tenancy foundation.

These cover the one thing that must never regress: a query made while a tenant
is active can only see that tenant's rows.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import connection, models
from django.test import TestCase

from apps.core.models import TenantScopedModel
from apps.core.roles import Role, Scope, scope_for
from apps.core.navigation import nav_for
from apps.core.tenancy import get_current_context, scope_to, unscoped
from apps.schools.models import Branch, School

User = get_user_model()


class ScopedThing(TenantScopedModel):
    """A stand-in for a real feature model (student, invoice, message).

    Declared here rather than shipped in ``models.py`` so the scaffold carries
    no junk table, with its table created and dropped around the test run.
    """

    name = models.CharField(max_length=50)

    class Meta(TenantScopedModel.Meta):
        app_label = "core"
        abstract = False


class TenancyTestCase(TestCase):
    """Two schools, three branches, one user per role."""

    @classmethod
    def setUpClass(cls):
        # Before super(), which opens the class-level atomic and runs
        # setUpTestData -- both of which need the table to already exist.
        with connection.schema_editor() as editor:
            editor.create_model(ScopedThing)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        with connection.schema_editor() as editor:
            editor.delete_model(ScopedThing)

    @classmethod
    def setUpTestData(cls):
        cls.acme = School.all_objects.create(name="Acme High")
        cls.rival = School.all_objects.create(name="Rival College")

        cls.acme_main = Branch.all_objects.create(school=cls.acme, name="Main")
        cls.acme_annex = Branch.all_objects.create(school=cls.acme, name="Annex")
        cls.rival_main = Branch.all_objects.create(school=cls.rival, name="Main")

        cls.platform = User.objects.create_user(
            "plat", role=Role.PLATFORM_OWNER, school=None
        )
        cls.acme_owner = User.objects.create_user(
            "owner", role=Role.SCHOOL_OWNER, school=cls.acme
        )
        cls.acme_bursar = User.objects.create_user(
            "bursar", role=Role.BURSAR, school=cls.acme, branch=cls.acme_main
        )

        # One row per branch, plus one school-wide row with no branch.
        cls.thing_main = ScopedThing.all_objects.create(
            school=cls.acme, branch=cls.acme_main, name="acme-main"
        )
        cls.thing_annex = ScopedThing.all_objects.create(
            school=cls.acme, branch=cls.acme_annex, name="acme-annex"
        )
        cls.thing_shared = ScopedThing.all_objects.create(
            school=cls.acme, branch=None, name="acme-shared"
        )
        cls.thing_rival = ScopedThing.all_objects.create(
            school=cls.rival, branch=cls.rival_main, name="rival"
        )

    def as_user(self, user):
        return scope_to(
            school_id=user.school_id, branch_id=user.branch_id, role=user.role
        )


class RoleScopeTests(TestCase):
    def test_each_role_maps_to_its_access_level(self):
        self.assertIs(scope_for(Role.PLATFORM_OWNER), Scope.PLATFORM)
        self.assertIs(scope_for(Role.SCHOOL_OWNER), Scope.SCHOOL)
        self.assertIs(scope_for(Role.PRINCIPAL), Scope.BRANCH)
        self.assertIs(scope_for(Role.BURSAR), Scope.BRANCH)

    def test_unknown_role_fails_closed(self):
        self.assertIs(scope_for("not-a-role"), Scope.BRANCH)
        self.assertIs(scope_for(None), Scope.BRANCH)


class NoContextTests(TenancyTestCase):
    def test_queries_outside_a_request_are_not_filtered(self):
        """Shell, migrations and management commands must see everything."""
        self.assertIsNone(get_current_context())
        self.assertEqual(ScopedThing.objects.count(), 4)
        self.assertEqual(School.objects.count(), 2)


class PlatformOwnerTests(TenancyTestCase):
    def test_sees_every_tenant(self):
        with self.as_user(self.platform):
            self.assertEqual(School.objects.count(), 2)
            self.assertEqual(Branch.objects.count(), 3)
            self.assertEqual(ScopedThing.objects.count(), 4)


class SchoolOwnerTests(TenancyTestCase):
    def test_sees_own_school_only(self):
        with self.as_user(self.acme_owner):
            self.assertEqual(list(School.objects.all()), [self.acme])

    def test_sees_every_branch_of_own_school(self):
        with self.as_user(self.acme_owner):
            self.assertCountEqual(
                Branch.objects.all(), [self.acme_main, self.acme_annex]
            )

    def test_never_sees_another_schools_rows(self):
        with self.as_user(self.acme_owner):
            names = set(ScopedThing.objects.values_list("name", flat=True))
        self.assertEqual(names, {"acme-main", "acme-annex", "acme-shared"})
        self.assertNotIn("rival", names)

    def test_get_on_another_tenants_row_raises_does_not_exist(self):
        with self.as_user(self.acme_owner):
            with self.assertRaises(ScopedThing.DoesNotExist):
                ScopedThing.objects.get(pk=self.thing_rival.pk)


class BranchScopedUserTests(TenancyTestCase):
    def test_sees_only_own_branch_plus_school_wide_rows(self):
        with self.as_user(self.acme_bursar):
            names = set(ScopedThing.objects.values_list("name", flat=True))
        # "acme-annex" belongs to a branch this bursar has no business seeing.
        self.assertEqual(names, {"acme-main", "acme-shared"})

    def test_branch_list_is_limited_to_own_branch(self):
        with self.as_user(self.acme_bursar):
            self.assertEqual(list(Branch.objects.all()), [self.acme_main])

    def test_branch_role_with_no_branch_assigned_sees_nothing(self):
        """A misconfigured account fails closed rather than seeing the school."""
        with scope_to(school_id=self.acme.pk, branch_id=None, role=Role.BURSAR):
            self.assertEqual(ScopedThing.objects.count(), 0)


class AnonymousTests(TenancyTestCase):
    def test_unauthenticated_context_sees_nothing(self):
        from apps.core.tenancy import TenantContext, activate, deactivate

        token = activate(TenantContext(is_authenticated=False))
        try:
            self.assertEqual(ScopedThing.objects.count(), 0)
            self.assertEqual(School.objects.count(), 0)
        finally:
            deactivate(token)


class EscapeHatchTests(TenancyTestCase):
    def test_unscoped_block_lifts_filtering(self):
        with self.as_user(self.acme_bursar):
            self.assertEqual(ScopedThing.objects.count(), 2)
            with unscoped():
                self.assertEqual(ScopedThing.objects.count(), 4)
            # ...and filtering is restored on the way out.
            self.assertEqual(ScopedThing.objects.count(), 2)

    def test_all_objects_manager_is_never_filtered(self):
        with self.as_user(self.acme_bursar):
            self.assertEqual(ScopedThing.all_objects.count(), 4)


class AutoStampTests(TenancyTestCase):
    def test_save_fills_school_and_branch_from_the_active_tenant(self):
        with self.as_user(self.acme_bursar):
            thing = ScopedThing.objects.create(name="new")
        thing.refresh_from_db()
        self.assertEqual(thing.school_id, self.acme.pk)
        self.assertEqual(thing.branch_id, self.acme_main.pk)

    def test_explicit_values_are_not_overwritten(self):
        with self.as_user(self.acme_owner):
            thing = ScopedThing.objects.create(name="annex", branch=self.acme_annex)
        self.assertEqual(thing.branch_id, self.acme_annex.pk)


class UserScopingTests(TenancyTestCase):
    def test_default_user_manager_stays_unscoped_for_authentication(self):
        """Auth backends resolve users before any tenant context exists."""
        with self.as_user(self.acme_bursar):
            self.assertTrue(User.objects.filter(username="plat").exists())

    def test_scoped_manager_limits_the_staff_directory(self):
        with self.as_user(self.acme_owner):
            usernames = set(User.scoped.values_list("username", flat=True))
        self.assertEqual(usernames, {"owner", "bursar"})


class NavigationTests(TestCase):
    def test_bursar_gets_no_platform_section(self):
        labels = {s.label for s in nav_for(Role.BURSAR, "/")}
        self.assertNotIn("Platform", labels)
        self.assertIn("Finance", labels)

    def test_platform_owner_gets_the_platform_section(self):
        labels = {s.label for s in nav_for(Role.PLATFORM_OWNER, "/")}
        self.assertIn("Platform", labels)

    def test_unbuilt_destinations_render_as_unavailable(self):
        sections = {s.label: s.items for s in nav_for(Role.SCHOOL_OWNER, "/")}
        branches = next(i for i in sections["School"] if i.label == "Branches")
        students = next(i for i in sections["School"] if i.label == "Students")
        self.assertTrue(branches.available)  # admin URL exists today
        self.assertFalse(students.available)  # feature screen not built yet

    def test_dashboard_is_marked_active_on_its_own_path(self):
        dashboard = nav_for(Role.BURSAR, "/")[0].items[0]
        self.assertTrue(dashboard.active)

    def test_anonymous_gets_no_navigation(self):
        self.assertEqual(nav_for(None), [])
