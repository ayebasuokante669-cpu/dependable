"""Tests for the academic-setup layer.

Two things must hold: a user only ever sees their own branch's classes and
subjects, and only owner/principal-level roles can change them.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.core.permissions import Capability, has_capability
from apps.core.roles import Role
from apps.core.tenancy import TenantContext, scope_to
from apps.schools.models import Branch, School

from .forms import SubjectForm
from .models import Class, Level, Subject

User = get_user_model()


class AcademicsTestCase(TestCase):
    """One school with two branches, plus a second school entirely."""

    @classmethod
    def setUpTestData(cls):
        cls.alpha = School.all_objects.create(name="Alpha Schools")
        cls.north = Branch.all_objects.create(school=cls.alpha, name="North")
        cls.south = Branch.all_objects.create(school=cls.alpha, name="South")

        cls.beta = School.all_objects.create(name="Beta College")
        cls.beta_main = Branch.all_objects.create(school=cls.beta, name="Beta Main")

        cls.alpha_owner = User.objects.create_user(
            "alpha.owner", password="pw", role=Role.SCHOOL_OWNER, school=cls.alpha
        )
        cls.north_principal = User.objects.create_user(
            "north.principal", password="pw", role=Role.PRINCIPAL,
            school=cls.alpha, branch=cls.north,
        )
        cls.north_bursar = User.objects.create_user(
            "north.bursar", password="pw", role=Role.BURSAR,
            school=cls.alpha, branch=cls.north,
        )
        cls.beta_owner = User.objects.create_user(
            "beta.owner", password="pw", role=Role.SCHOOL_OWNER, school=cls.beta
        )

        cls.north_jss1 = Class.all_objects.create(
            branch=cls.north, name="JSS 1", level=Level.JUNIOR_SECONDARY, year_in_level=1
        )
        cls.south_jss1 = Class.all_objects.create(
            branch=cls.south, name="JSS 1", level=Level.JUNIOR_SECONDARY, year_in_level=1
        )
        cls.beta_jss1 = Class.all_objects.create(
            branch=cls.beta_main, name="JSS 1", level=Level.JUNIOR_SECONDARY,
            year_in_level=1,
        )

        cls.north_maths = Subject.all_objects.create(branch=cls.north, name="Mathematics")
        cls.north_maths.classes.set([cls.north_jss1])
        cls.south_maths = Subject.all_objects.create(branch=cls.south, name="Mathematics")

    def as_user(self, user):
        return scope_to(
            school_id=user.school_id, branch_id=user.branch_id, role=user.role
        )


class ModelTests(AcademicsTestCase):
    def test_school_is_derived_from_the_branch(self):
        """A platform owner has no school of their own; the branch supplies it."""
        klass = Class.all_objects.create(
            branch=self.north, name="Primary 1", level=Level.PRIMARY, year_in_level=1
        )
        self.assertEqual(klass.school_id, self.alpha.pk)

    def test_display_name_appends_single_letter_arms(self):
        klass = Class(name="JSS 1", stream="A")
        self.assertEqual(klass.display_name, "JSS 1A")

    def test_display_name_spaces_word_arms(self):
        klass = Class(name="SSS 2", stream="Science")
        self.assertEqual(klass.display_name, "SSS 2 Science")

    def test_display_name_without_an_arm_is_just_the_name(self):
        self.assertEqual(Class(name="Primary 3").display_name, "Primary 3")

    def test_classes_list_in_admission_order(self):
        for name, level, year in [
            ("SSS 1", Level.SENIOR_SECONDARY, 1),
            ("Pre-KG", Level.NURSERY, 0),
            ("Primary 2", Level.PRIMARY, 2),
            ("KG 1", Level.NURSERY, 1),
            ("Primary 1", Level.PRIMARY, 1),
        ]:
            Class.all_objects.create(
                branch=self.north, name=name, level=level, year_in_level=year
            )
        with self.as_user(self.north_principal):
            ordered = [c.name for c in Class.objects.all()]
        self.assertEqual(
            ordered, ["Pre-KG", "KG 1", "Primary 1", "Primary 2", "JSS 1", "SSS 1"]
        )

    def test_a_subject_can_span_many_classes(self):
        primary = Class.all_objects.create(
            branch=self.north, name="Primary 6", level=Level.PRIMARY, year_in_level=6
        )
        self.north_maths.classes.add(primary)
        self.assertCountEqual(
            self.north_maths.classes.all(), [self.north_jss1, primary]
        )


class ScopingTests(AcademicsTestCase):
    def test_principal_sees_only_their_own_branch(self):
        with self.as_user(self.north_principal):
            self.assertEqual(list(Class.objects.all()), [self.north_jss1])
            self.assertEqual(list(Subject.objects.all()), [self.north_maths])

    def test_school_owner_sees_every_branch_of_their_school(self):
        with self.as_user(self.alpha_owner):
            self.assertCountEqual(
                Class.objects.all(), [self.north_jss1, self.south_jss1]
            )

    def test_another_school_is_never_visible(self):
        with self.as_user(self.alpha_owner):
            self.assertNotIn(self.beta_jss1, Class.objects.all())
        with self.as_user(self.beta_owner):
            self.assertEqual(list(Class.objects.all()), [self.beta_jss1])

    def test_same_class_name_may_exist_at_each_branch(self):
        """The uniqueness constraint is per branch, not per school."""
        self.assertEqual(
            Class.all_objects.filter(name="JSS 1").count(), 3
        )


class CapabilityTests(TestCase):
    # An unsaved User instance already reports is_authenticated == True, so
    # these need no database and no login.

    def test_owner_and_principal_can_manage_academics(self):
        for role in (Role.PLATFORM_OWNER, Role.SCHOOL_OWNER, Role.PRINCIPAL):
            user = User(role=role, is_active=True)
            self.assertTrue(has_capability(user, Capability.MANAGE_ACADEMICS), role)

    def test_bursar_may_view_but_not_manage(self):
        user = User(role=Role.BURSAR)
        self.assertTrue(has_capability(user, Capability.VIEW_ACADEMICS))
        self.assertFalse(has_capability(user, Capability.MANAGE_ACADEMICS))

    def test_anonymous_holds_nothing(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(has_capability(AnonymousUser(), Capability.VIEW_ACADEMICS))

    def test_superuser_gets_everything(self):
        user = User(role=Role.BURSAR, is_superuser=True)
        self.assertTrue(has_capability(user, Capability.MANAGE_ACADEMICS))

    def test_plain_superuser_is_scoped_as_platform_staff(self):
        """Regression: createsuperuser leaves role=bursar and school=None."""
        user = User(username="root", role=Role.BURSAR, is_superuser=True)
        context = TenantContext.from_user(user)
        self.assertEqual(context.role, Role.PLATFORM_OWNER)


class ViewScopingTests(AcademicsTestCase):
    def test_class_list_shows_only_the_users_branch(self):
        self.client.force_login(self.north_principal)
        response = self.client.get(reverse("academics:class_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["classes"]), [self.north_jss1])

    def test_editing_another_branchs_class_is_a_404_not_a_403(self):
        """The row is invisible, so it must look absent rather than forbidden."""
        self.client.force_login(self.north_principal)
        response = self.client.get(
            reverse("academics:class_update", args=[self.south_jss1.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_editing_another_schools_class_is_a_404(self):
        self.client.force_login(self.alpha_owner)
        response = self.client.get(
            reverse("academics:class_update", args=[self.beta_jss1.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_subject_form_only_offers_classes_in_scope(self):
        self.client.force_login(self.north_principal)
        response = self.client.get(reverse("academics:subject_create"))
        offered = set(response.context["form"].fields["classes"].queryset)
        self.assertEqual(offered, {self.north_jss1})


class ViewPermissionTests(AcademicsTestCase):
    manage_urls = [
        ("academics:class_create", ()),
        ("academics:subject_create", ()),
    ]

    def test_bursar_can_read_both_lists(self):
        self.client.force_login(self.north_bursar)
        for name in ("academics:class_list", "academics:subject_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_bursar_cannot_reach_create_screens(self):
        self.client.force_login(self.north_bursar)
        for name, args in self.manage_urls:
            self.assertEqual(
                self.client.get(reverse(name, args=args)).status_code, 403, name
            )

    def test_bursar_cannot_edit_or_delete(self):
        self.client.force_login(self.north_bursar)
        for name in ("academics:class_update", "academics:class_delete"):
            url = reverse(name, args=[self.north_jss1.pk])
            self.assertEqual(self.client.get(url).status_code, 403, name)

    def test_bursar_cannot_post_a_new_class(self):
        """The block is on dispatch, so POST is refused too, not just GET."""
        self.client.force_login(self.north_bursar)
        before = Class.all_objects.count()
        response = self.client.post(
            reverse("academics:class_create"),
            {"branch": self.north.pk, "name": "Sneaky", "level": Level.PRIMARY,
             "year_in_level": 1, "stream": ""},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Class.all_objects.count(), before)

    def test_bursar_list_hides_the_edit_controls(self):
        self.client.force_login(self.north_bursar)
        body = self.client.get(reverse("academics:class_list")).content.decode()
        self.assertNotIn("New class", body)

    def test_principal_sees_the_edit_controls(self):
        """Asserted by destination rather than by button label.

        The list now offers three ways in -- add many, add one, edit all -- and
        their wording is a copy decision that should be free to change. What
        must hold is that a principal is offered the screens their capability
        allows, which is what the URLs say.
        """
        self.client.force_login(self.north_principal)
        body = self.client.get(reverse("academics:class_list")).content.decode()
        self.assertIn(reverse("academics:class_create"), body)
        self.assertIn(reverse("academics:class_bulk_create"), body)
        self.assertIn(reverse("academics:class_edit_all"), body)

    def test_principal_can_create_a_class(self):
        self.client.force_login(self.north_principal)
        response = self.client.post(
            reverse("academics:class_create"),
            {"branch": self.north.pk, "name": "Primary 4", "level": Level.PRIMARY,
             "year_in_level": 4, "stream": "", "is_active": "on"},
        )
        self.assertRedirects(response, reverse("academics:class_list"))
        created = Class.all_objects.get(branch=self.north, name="Primary 4")
        self.assertEqual(created.school_id, self.alpha.pk)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("academics:class_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.headers["Location"])


class SubjectFormTests(AcademicsTestCase):
    def test_classes_from_another_branch_are_rejected(self):
        """A school owner can see both branches, but must not wire them together."""
        with self.as_user(self.alpha_owner):
            form = SubjectForm(
                data={
                    "branch": self.north.pk,
                    "name": "Chemistry",
                    "code": "CHM",
                    "classes": [self.south_jss1.pk],
                    "is_active": True,
                }
            )
            self.assertFalse(form.is_valid())
            self.assertIn("classes", form.errors)

    def test_valid_subject_saves_with_the_school_derived(self):
        with self.as_user(self.alpha_owner):
            form = SubjectForm(
                data={
                    "branch": self.north.pk,
                    "name": "Physics",
                    "code": "PHY",
                    "classes": [self.north_jss1.pk],
                    "is_active": True,
                }
            )
            self.assertTrue(form.is_valid(), form.errors)
            subject = form.save()
        self.assertEqual(subject.school_id, self.alpha.pk)
        self.assertEqual(list(subject.classes.all()), [self.north_jss1])

    def test_branch_choices_are_limited_to_what_the_user_can_see(self):
        with self.as_user(self.north_principal):
            form = SubjectForm()
            self.assertEqual(list(form.fields["branch"].queryset), [self.north])


class SeedCommandTests(TestCase):
    def test_seeding_is_idempotent_and_builds_the_full_ladder(self):
        from django.core.management import call_command
        from io import StringIO

        out = StringIO()
        call_command("seed_academics", "--create-school", stdout=out)
        first_classes = Class.all_objects.count()
        first_subjects = Subject.all_objects.count()

        # 13 single-arm classes + 3 senior years x 2 arms.
        self.assertEqual(first_classes, 19)
        self.assertEqual(first_subjects, 34)

        call_command("seed_academics", stdout=out)
        self.assertEqual(Class.all_objects.count(), first_classes)
        self.assertEqual(Subject.all_objects.count(), first_subjects)

    def test_every_subject_lands_on_at_least_one_class(self):
        from django.core.management import call_command
        from io import StringIO

        call_command("seed_academics", "--create-school", stdout=StringIO())
        orphans = [s.name for s in Subject.all_objects.all() if not s.classes.exists()]
        self.assertEqual(orphans, [])

    def test_a_subject_spanning_levels_is_one_row_reaching_all_of_them(self):
        from django.core.management import call_command
        from io import StringIO

        call_command("seed_academics", "--create-school", stdout=StringIO())

        # Nursery through JSS 3, one row: 4 nursery + 6 primary + 3 junior.
        # The senior arms teach their own religion subject by name instead.
        religion = Subject.all_objects.get(name="Religion Studies")
        self.assertEqual(religion.classes.count(), 13)
        self.assertNotIn(
            Level.SENIOR_SECONDARY, {c.level for c in religion.classes.all()}
        )

        # Mathematics starts at primary -- nursery has Number Work instead.
        maths = Subject.all_objects.get(name="Mathematics")
        self.assertEqual(
            {Level(c.level).label for c in maths.classes.all()},
            {"Primary", "Junior Secondary", "Senior Secondary"},
        )

    def test_nursery_subjects_do_not_leak_into_primary(self):
        from django.core.management import call_command
        from io import StringIO

        call_command("seed_academics", "--create-school", stdout=StringIO())
        rhymes = Subject.all_objects.get(name="Rhymes & Poems")
        self.assertEqual(
            {Level(c.level).label for c in rhymes.classes.all()}, {"Nursery"}
        )

    def test_the_shared_core_is_one_row_on_both_senior_arms(self):
        """Mathematics is not duplicated per arm -- one row, six senior classes."""
        from django.core.management import call_command
        from io import StringIO

        call_command("seed_academics", "--create-school", stdout=StringIO())

        maths = Subject.all_objects.get(name="Mathematics")
        senior = [c for c in maths.classes.all() if c.level == Level.SENIOR_SECONDARY]
        self.assertEqual({c.stream for c in senior}, {"Arts", "Science"})
        self.assertEqual(len(senior), 6)  # 3 years x 2 arms
        self.assertEqual(
            Subject.all_objects.filter(name="Mathematics").count(), 1
        )

    def test_a_junior_subject_does_not_leak_into_the_senior_arms(self):
        from django.core.management import call_command
        from io import StringIO

        call_command("seed_academics", "--create-school", stdout=StringIO())

        digital = Subject.all_objects.get(name="Digital Literacy")
        self.assertEqual(
            {Level(c.level).label for c in digital.classes.all()},
            {"Junior Secondary"},
        )
        self.assertEqual(digital.classes.count(), 3)

    def test_arm_specific_placement_narrows_to_its_arm(self):
        """Physics is a Science subject; the Arts arm must not receive it."""
        from django.core.management import call_command
        from io import StringIO

        call_command("seed_academics", "--create-school", stdout=StringIO())

        physics = Subject.all_objects.get(name="Physics")
        arms = {c.stream for c in physics.classes.all()}
        self.assertEqual(arms, {"Science"})
        self.assertNotIn("Arts", arms)
        self.assertEqual(physics.classes.count(), 3)  # SSS 1-3 Science

        science = Class.all_objects.get(name="SSS 2", stream="Science")
        arts = Class.all_objects.get(name="SSS 2", stream="Arts")
        self.assertIn("Physics", {s.name for s in science.subjects.all()})
        self.assertNotIn("Physics", {s.name for s in arts.subjects.all()})

        # ...and the mirror case, so neither arm is simply getting everything.
        self.assertIn("Government", {s.name for s in arts.subjects.all()})
        self.assertNotIn("Government", {s.name for s in science.subjects.all()})

    def test_each_senior_arm_carries_the_subjects_the_school_named(self):
        """The client's two lists, exactly -- eleven subjects on each arm."""
        from django.core.management import call_command
        from io import StringIO

        call_command("seed_academics", "--create-school", stdout=StringIO())

        expected = {
            "Science": {
                "Mathematics", "English Studies", "Chemistry", "Biology", "Physics",
                "Agriculture", "Geography", "Economics", "Marketing",
                "Civic Education", "Livestock",
            },
            "Arts": {
                "Mathematics", "English Studies", "Commerce", "Economics",
                "Accounting", "Government", "Literature in English",
                "Christian Religious Knowledge", "Civic Education", "Marketing",
                "Home Management",
            },
        }
        for klass in Class.all_objects.filter(level=Level.SENIOR_SECONDARY):
            taught = {s.name for s in klass.subjects.all()}
            self.assertEqual(taught, expected[klass.stream], klass.display_name)
            self.assertEqual(len(taught), 11, klass.display_name)

    def test_refuses_an_unknown_school_without_the_flag(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("seed_academics", "--school", "Nope")


class NameInferenceTests(TestCase):
    """Reading a level and a year off a class name.

    These are defaults offered to a form field, never silent writes, so the
    bar is "right often enough to save typing" rather than "right always".
    What must hold is that an unreadable name returns None instead of
    guessing, because a wrong band sorts the class into the wrong place on
    every screen afterwards.
    """

    def test_the_common_nigerian_spellings_are_read(self):
        from .curriculum import infer_level

        cases = {
            "Pre-KG": Level.NURSERY,
            "KG 2": Level.NURSERY,
            "Nursery 1": Level.NURSERY,
            "Reception": Level.NURSERY,
            "Primary 4": Level.PRIMARY,
            "Grade 5": Level.PRIMARY,
            "JSS 1": Level.JUNIOR_SECONDARY,
            "JSS 2A": Level.JUNIOR_SECONDARY,
            "SSS 3": Level.SENIOR_SECONDARY,
            "SS 2": Level.SENIOR_SECONDARY,
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(infer_level(name), expected)

    def test_basic_is_decided_by_the_number_beside_it(self):
        """Basic 1-6 is primary; Basic 7-9 is junior secondary."""
        from .curriculum import infer_level

        self.assertEqual(infer_level("Basic 3"), Level.PRIMARY)
        self.assertEqual(infer_level("Basic 8"), Level.JUNIOR_SECONDARY)

    def test_an_unreadable_name_is_not_guessed_at(self):
        from .curriculum import infer_level

        self.assertIsNone(infer_level("Butterfly Group"))
        self.assertIsNone(infer_level(""))

    def test_the_year_is_the_last_number_in_the_name(self):
        from .curriculum import infer_year

        self.assertEqual(infer_year("Primary 4"), 4)
        self.assertEqual(infer_year("JSS 2A"), 2)
        self.assertIsNone(infer_year("Pre-KG"))
        # Not a year: a year of entry that has ended up in the name field.
        self.assertIsNone(infer_year("Intake 2026"))


class LadderPresetTests(TestCase):
    def test_the_ladder_matches_the_seed_list(self):
        """The preset buttons and `seed_academics` must describe one ladder."""
        from .curriculum import CLASSES, ladder_presets

        rows = [row for group in ladder_presets() for row in group["rows"]]
        expected = sum(len(spec.streams) or 1 for spec in CLASSES)
        self.assertEqual(len(rows), expected)

    def test_an_armed_rung_becomes_one_row_per_arm(self):
        from .curriculum import ladder_presets

        senior = next(
            group for group in ladder_presets()
            if group["label"] == Level.SENIOR_SECONDARY.label
        )
        sss1 = [row for row in senior["rows"] if row["name"] == "SSS 1"]
        self.assertEqual({row["stream"] for row in sss1}, {"Arts", "Science"})


class ClassBulkCreateTests(AcademicsTestCase):
    """The screen that replaced nineteen round trips with one."""

    url = "academics:class_bulk_create"

    def rows(self, *entries, prefix="form", total=None):
        """POST data for a bulk submission, management form included."""
        data = {
            f"{prefix}-TOTAL_FORMS": str(total if total is not None else len(entries)),
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
        for index, entry in enumerate(entries):
            for field in ("name", "level", "year_in_level", "stream"):
                data[f"{prefix}-{index}-{field}"] = entry.get(field, "")
        return data

    def test_the_principal_can_add_several_classes_in_one_post(self):
        self.client.force_login(self.north_principal)
        before = Class.all_objects.filter(branch=self.north).count()

        response = self.client.post(
            reverse(self.url),
            {
                "branch": str(self.north.pk),
                **self.rows(
                    {"name": "Primary 1"},
                    {"name": "Primary 2"},
                    {"name": "Primary 3"},
                ),
            },
        )

        self.assertRedirects(response, reverse("academics:class_list"))
        self.assertEqual(
            Class.all_objects.filter(branch=self.north).count(), before + 3
        )

    def test_the_level_and_year_are_filled_in_from_the_name(self):
        self.client.force_login(self.north_principal)
        self.client.post(
            reverse(self.url),
            {"branch": str(self.north.pk), **self.rows({"name": "Primary 5"})},
        )
        klass = Class.all_objects.get(branch=self.north, name="Primary 5")
        self.assertEqual(klass.level, Level.PRIMARY)
        self.assertEqual(klass.year_in_level, 5)

    def test_a_typed_level_beats_the_guess(self):
        """The inference is a default, so an explicit choice must win."""
        self.client.force_login(self.north_principal)
        self.client.post(
            reverse(self.url),
            {
                "branch": str(self.north.pk),
                **self.rows(
                    {"name": "Primary 5", "level": str(Level.JUNIOR_SECONDARY)}
                ),
            },
        )
        klass = Class.all_objects.get(branch=self.north, name="Primary 5")
        self.assertEqual(klass.level, Level.JUNIOR_SECONDARY)

    def test_blank_rows_are_ignored_rather_than_rejected(self):
        self.client.force_login(self.north_principal)
        response = self.client.post(
            reverse(self.url),
            {
                "branch": str(self.north.pk),
                **self.rows(
                    {"name": "Primary 1"}, {}, {}, {},
                ),
            },
        )
        self.assertRedirects(response, reverse("academics:class_list"))
        self.assertEqual(
            Class.all_objects.filter(branch=self.north, name="Primary 1").count(), 1
        )

    def test_a_name_that_cannot_be_read_asks_for_the_level(self):
        self.client.force_login(self.north_principal)
        response = self.client.post(
            reverse(self.url),
            {"branch": str(self.north.pk), **self.rows({"name": "Butterfly Group"})},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Class.all_objects.filter(branch=self.north, name="Butterfly Group").exists()
        )
        self.assertIn("level", response.context["formset"].forms[0].errors)

    def test_two_rows_naming_the_same_class_is_a_row_error(self):
        self.client.force_login(self.north_principal)
        response = self.client.post(
            reverse(self.url),
            {
                "branch": str(self.north.pk),
                **self.rows({"name": "Primary 1"}, {"name": "primary 1"}),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("name", response.context["formset"].forms[1].errors)
        self.assertFalse(Class.all_objects.filter(name__iexact="Primary 1").exists())

    def test_a_row_clashing_with_an_existing_class_is_caught(self):
        """The branch is not a row field, so only the formset can see this."""
        self.client.force_login(self.north_principal)
        response = self.client.post(
            reverse(self.url),
            {"branch": str(self.north.pk), **self.rows({"name": "JSS 1"})},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("name", response.context["formset"].forms[0].errors)
        self.assertEqual(
            Class.all_objects.filter(branch=self.north, name="JSS 1").count(), 1
        )

    def test_nothing_is_written_when_one_row_is_bad(self):
        """One transaction: nine of nineteen saved is worse than none."""
        self.client.force_login(self.north_principal)
        before = Class.all_objects.count()
        self.client.post(
            reverse(self.url),
            {
                "branch": str(self.north.pk),
                **self.rows({"name": "Primary 1"}, {"name": "Butterfly Group"}),
            },
        )
        self.assertEqual(Class.all_objects.count(), before)

    def test_save_and_add_more_comes_back_to_the_table(self):
        self.client.force_login(self.north_principal)
        url = reverse(self.url)
        response = self.client.post(
            url,
            {
                "branch": str(self.north.pk),
                "save_and_add_another": "1",
                **self.rows({"name": "Primary 1"}),
            },
        )
        self.assertRedirects(response, url)

    def test_a_principal_cannot_add_to_another_campus(self):
        """The campus select is a scoped queryset, so South is not a choice."""
        self.client.force_login(self.north_principal)
        response = self.client.post(
            reverse(self.url),
            {"branch": str(self.south.pk), **self.rows({"name": "Primary 1"})},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Class.all_objects.filter(branch=self.south, name="Primary 1").exists()
        )

    def test_a_bursar_cannot_reach_it(self):
        self.client.force_login(self.north_bursar)
        self.assertEqual(self.client.get(reverse(self.url)).status_code, 403)
        before = Class.all_objects.count()
        self.assertEqual(
            self.client.post(
                reverse(self.url),
                {"branch": str(self.north.pk), **self.rows({"name": "Primary 1"})},
            ).status_code,
            403,
        )
        self.assertEqual(Class.all_objects.count(), before)


class ClassEditAllTests(AcademicsTestCase):
    """Editing every class in place, in one submission."""

    url = "academics:class_edit_all"

    def rows(self, *entries, prefix="form"):
        """POST data for the edit table. Entries are dicts per rendered row."""
        data = {
            f"{prefix}-TOTAL_FORMS": str(len(entries)),
            f"{prefix}-INITIAL_FORMS": str(len(entries)),
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
        for index, entry in enumerate(entries):
            data[f"{prefix}-{index}-id"] = str(entry["id"])
            for field in ("name", "level", "year_in_level", "stream"):
                data[f"{prefix}-{index}-{field}"] = entry.get(field, "")
            if entry.get("is_active", True):
                data[f"{prefix}-{index}-is_active"] = "on"
        return data

    def test_a_class_is_renamed_without_visiting_its_own_page(self):
        self.client.force_login(self.north_principal)
        response = self.client.post(
            reverse(self.url),
            self.rows({
                "id": self.north_jss1.pk, "name": "JSS One",
                "level": str(Level.JUNIOR_SECONDARY), "year_in_level": "1",
            }),
        )
        self.assertRedirects(response, reverse("academics:class_list"))
        self.north_jss1.refresh_from_db()
        self.assertEqual(self.north_jss1.name, "JSS One")

    def test_a_class_can_be_deactivated_rather_than_deleted(self):
        self.client.force_login(self.north_principal)
        self.client.post(
            reverse(self.url),
            self.rows({
                "id": self.north_jss1.pk, "name": "JSS 1",
                "level": str(Level.JUNIOR_SECONDARY), "year_in_level": "1",
                "is_active": False,
            }),
        )
        self.north_jss1.refresh_from_db()
        self.assertFalse(self.north_jss1.is_active)
        # Still there, with its history.
        self.assertTrue(Class.all_objects.filter(pk=self.north_jss1.pk).exists())

    def test_the_screen_only_offers_classes_the_user_may_see(self):
        self.client.force_login(self.north_principal)
        response = self.client.get(reverse(self.url))
        editing = {form.instance.pk for form in response.context["formset"].forms}
        self.assertEqual(editing, {self.north_jss1.pk})

    def test_renaming_into_a_clash_at_the_same_campus_is_refused(self):
        other = Class.all_objects.create(
            branch=self.north, name="JSS 2", level=Level.JUNIOR_SECONDARY,
            year_in_level=2,
        )
        self.client.force_login(self.north_principal)
        response = self.client.post(
            reverse(self.url),
            self.rows(
                {"id": self.north_jss1.pk, "name": "JSS 1",
                 "level": str(Level.JUNIOR_SECONDARY), "year_in_level": "1"},
                {"id": other.pk, "name": "jss 1",
                 "level": str(Level.JUNIOR_SECONDARY), "year_in_level": "2"},
            ),
        )
        self.assertEqual(response.status_code, 200)
        other.refresh_from_db()
        self.assertEqual(other.name, "JSS 2")

    def test_the_same_name_at_a_different_campus_is_allowed(self):
        """An owner editing two campuses must not be blocked by the other's names."""
        self.client.force_login(self.alpha_owner)
        response = self.client.post(
            reverse(self.url),
            self.rows(
                {"id": self.north_jss1.pk, "name": "JSS 1",
                 "level": str(Level.JUNIOR_SECONDARY), "year_in_level": "1"},
                {"id": self.south_jss1.pk, "name": "JSS 1",
                 "level": str(Level.JUNIOR_SECONDARY), "year_in_level": "1"},
            ),
        )
        self.assertRedirects(response, reverse("academics:class_list"))

    def test_a_bursar_cannot_reach_it(self):
        self.client.force_login(self.north_bursar)
        self.assertEqual(self.client.get(reverse(self.url)).status_code, 403)


class SubjectBulkCreateTests(AcademicsTestCase):
    url = "academics:subject_bulk_create"

    def rows(self, *entries, prefix="form"):
        data = {
            f"{prefix}-TOTAL_FORMS": str(len(entries)),
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }
        for index, entry in enumerate(entries):
            for field in ("name", "code"):
                data[f"{prefix}-{index}-{field}"] = entry.get(field, "")
        return data

    def test_several_subjects_are_added_in_one_post(self):
        self.client.force_login(self.north_principal)
        response = self.client.post(
            reverse(self.url),
            {
                "branch": str(self.north.pk),
                **self.rows(
                    {"name": "Physics", "code": "phy"},
                    {"name": "Chemistry"},
                ),
            },
        )
        self.assertRedirects(response, reverse("academics:subject_list"))
        self.assertTrue(
            Subject.all_objects.filter(branch=self.north, name="Physics").exists()
        )
        # Codes are stored upper-cased whatever was typed.
        self.assertEqual(
            Subject.all_objects.get(branch=self.north, name="Physics").code, "PHY"
        )

    def test_the_batch_classes_are_attached_to_every_subject(self):
        self.client.force_login(self.north_principal)
        self.client.post(
            reverse(self.url),
            {
                "branch": str(self.north.pk),
                "classes": [str(self.north_jss1.pk)],
                **self.rows({"name": "Physics"}, {"name": "Chemistry"}),
            },
        )
        for name in ("Physics", "Chemistry"):
            subject = Subject.all_objects.get(branch=self.north, name=name)
            self.assertEqual(list(subject.classes.all()), [self.north_jss1])

    def test_a_class_at_another_campus_is_not_attached(self):
        """The same guard SubjectForm.clean applies, at batch scale."""
        self.client.force_login(self.alpha_owner)
        self.client.post(
            reverse(self.url),
            {
                "branch": str(self.north.pk),
                "classes": [str(self.south_jss1.pk)],
                **self.rows({"name": "Physics"}),
            },
        )
        subject = Subject.all_objects.get(branch=self.north, name="Physics")
        self.assertEqual(list(subject.classes.all()), [])

    def test_a_subject_the_campus_already_teaches_is_refused(self):
        self.client.force_login(self.north_principal)
        response = self.client.post(
            reverse(self.url),
            {"branch": str(self.north.pk), **self.rows({"name": "Mathematics"})},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("name", response.context["formset"].forms[0].errors)
        self.assertEqual(
            Subject.all_objects.filter(branch=self.north, name="Mathematics").count(), 1
        )

    def test_a_bursar_cannot_reach_it(self):
        self.client.force_login(self.north_bursar)
        self.assertEqual(self.client.get(reverse(self.url)).status_code, 403)


class NavigationTests(TestCase):
    def test_every_role_gets_the_academics_section(self):
        from apps.core.navigation import nav_for

        for role in Role.values:
            labels = {s.label for s in nav_for(role, "/")}
            self.assertIn("Academics", labels, role)

    def test_academics_links_resolve_now_that_the_app_exists(self):
        from apps.core.navigation import nav_for

        sections = {s.label: s.items for s in nav_for(Role.PRINCIPAL, "/")}
        for item in sections["Academics"]:
            self.assertTrue(item.available, item.label)
