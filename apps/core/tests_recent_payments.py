"""The recent-payments feed on the owner's, principal's and bursar's dashboards.

Who sees it, whose money is in it, and which payments come first.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import Class, Level
from apps.core.roles import Role
from apps.core.views import RECENT_PAYMENTS_LIMIT
from apps.fees.models import Term, TermSequence
from apps.payments.models import PaymentStatus
from apps.payments.tests import PaymentsTestCase
from apps.schools.models import Branch, School

User = get_user_model()

class RecentPaymentsTestCase(PaymentsTestCase):
    """Alpha's two campuses from PaymentsTestCase, plus a second school, Beta."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        today = timezone.localdate()

        cls.beta = School.all_objects.create(name="Beta College")
        beta_main = Branch.all_objects.create(school=cls.beta, name="Main")
        beta_class = Class.all_objects.create(
            branch=beta_main, name="Primary 1", level=Level.PRIMARY, year_in_level=1
        )
        beta_term = Term.all_objects.create(
            branch=beta_main, name="First Term 2025/2026", academic_year="2025/2026",
            sequence=TermSequence.FIRST, is_current=True,
        )
        cls.beta_owner = User.objects.create_user(
            "beta.owner", password="pw", role=Role.SCHOOL_OWNER, school=cls.beta
        )
        beta_kid = cls.make_student("B/001", "Bisi", "Lawal", beta_class)

        cls.north_old = cls.pay(cls.ada, "11000", date_paid=today - timedelta(days=9))
        cls.north_new = cls.pay(cls.bola, "22000", date_paid=today - timedelta(days=1))
        cls.north_pending = cls.pay(
            cls.chidi, "33000", status=PaymentStatus.PENDING, date_paid=today
        )
        cls.north_void = cls.pay(cls.dami, "44000", date_paid=today)
        cls.north_void.void(reason="Typed twice")
        cls.south_payment = cls.pay(
            cls.south_kid, "55000", date_paid=today - timedelta(days=3)
        )
        cls.beta_payment = cls.pay(
            beta_kid, "66000", term=beta_term, date_paid=today
        )

    def feed_on(self, user, url_name):
        self.client.force_login(user)
        response = self.client.get(reverse(url_name))
        self.assertEqual(response.status_code, 200)
        return response

    @staticmethod
    def pks(response):
        return [p.pk for p in response.context["recent_payments"]]


class RoleVisibilityTests(RecentPaymentsTestCase):
    def test_every_money_role_gets_the_section(self):
        for user, url_name in (
            (self.owner, "core:school_dashboard"),
            (self.principal, "core:branch_dashboard"),
            (self.bursar, "core:bursar_dashboard"),
        ):
            with self.subTest(user=user.username):
                response = self.feed_on(user, url_name)
                self.assertContains(response, "Recent payments")
                self.assertContains(
                    response, f'href="{reverse("payments:index")}"'
                )

    def test_each_row_links_to_its_payment(self):
        response = self.feed_on(self.bursar, "core:bursar_dashboard")
        for payment in response.context["recent_payments"]:
            with self.subTest(payment=payment.pk):
                self.assertContains(
                    response,
                    f'href="{reverse("payments:payment_detail", args=[payment.pk])}"',
                )

    def test_the_platform_dashboard_has_no_feed(self):
        platform = User.objects.create_user(
            "platform", password="pw", role=Role.PLATFORM_OWNER
        )
        self.client.force_login(platform)
        response = self.client.get(reverse("core:platform_overview"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Recent payments")

    def test_a_school_with_no_payments_gets_an_empty_state(self):
        gamma = School.all_objects.create(name="Gamma")
        Branch.all_objects.create(school=gamma, name="Only")
        owner = User.objects.create_user(
            "gamma.owner", password="pw", role=Role.SCHOOL_OWNER, school=gamma
        )
        response = self.feed_on(owner, "core:school_dashboard")
        self.assertEqual(response.context["recent_payments"], [])
        self.assertContains(response, "No payments recorded yet.")


class TenantScopingTests(RecentPaymentsTestCase):
    def test_the_owner_sees_every_campus_of_their_own_school_only(self):
        pks = self.pks(self.feed_on(self.owner, "core:school_dashboard"))
        self.assertIn(self.north_new.pk, pks)
        self.assertIn(self.south_payment.pk, pks)
        self.assertNotIn(self.beta_payment.pk, pks)

    def test_a_principal_sees_their_own_campus_only(self):
        pks = self.pks(self.feed_on(self.principal, "core:branch_dashboard"))
        self.assertIn(self.north_new.pk, pks)
        self.assertNotIn(self.south_payment.pk, pks)
        self.assertNotIn(self.beta_payment.pk, pks)

    def test_a_bursar_sees_their_own_campus_only(self):
        north = self.pks(self.feed_on(self.bursar, "core:bursar_dashboard"))
        south = self.pks(self.feed_on(self.south_bursar, "core:bursar_dashboard"))
        self.assertNotIn(self.south_payment.pk, north)
        self.assertEqual(south, [self.south_payment.pk])

    def test_another_schools_owner_sees_only_theirs(self):
        pks = self.pks(self.feed_on(self.beta_owner, "core:school_dashboard"))
        self.assertEqual(pks, [self.beta_payment.pk])


class OrderingTests(RecentPaymentsTestCase):
    def test_newest_first_with_pending_shown_and_voided_left_out(self):
        response = self.feed_on(self.principal, "core:branch_dashboard")
        self.assertEqual(
            self.pks(response),
            [self.north_pending.pk, self.north_new.pk, self.north_old.pk],
        )
        self.assertContains(response, "Pending")
        self.assertContains(response, "Confirmed")
        self.assertNotIn(self.north_void.pk, self.pks(response))

    def test_same_day_payments_put_the_later_entry_first(self):
        today = timezone.localdate()
        first = self.pay(self.ada, "1000", date_paid=today)
        second = self.pay(self.bola, "2000", date_paid=today)
        pks = self.pks(self.feed_on(self.bursar, "core:bursar_dashboard"))
        self.assertLess(pks.index(second.pk), pks.index(first.pk))

    def test_the_feed_is_capped(self):
        today = timezone.localdate()
        for _ in range(RECENT_PAYMENTS_LIMIT + 3):
            self.pay(self.ada, "100", date_paid=today)
        pks = self.pks(self.feed_on(self.bursar, "core:bursar_dashboard"))
        self.assertEqual(len(pks), RECENT_PAYMENTS_LIMIT)
