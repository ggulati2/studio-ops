"""Contract editors, delivery days on calendars, delays, copy-down and auto-planning."""
from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from ops import services as S
from ops.models import ContractPayment, DayLog, DelayReport, PlanEntry, Profile, Project

from .base import SeededTestCase

U = get_user_model()


def next_workday(d, user):
    while not S.is_workday_for(user, d):
        d += timedelta(days=1)
    return d


class AutoPlanner(SeededTestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.harsh = U.objects.get(username="harsh")
        from ops.models import Agency
        self.agency = Agency.objects.get(name="BadalRaja Company")

    def test_new_project_with_editor_and_deadline_fills_planner(self):
        dl = self.today + timedelta(days=20)
        PlanEntry.objects.filter(user=self.harsh, date__gte=self.today).delete()
        r = self.login("ag").post("/projects/new/", {"couple": "Plan Test", "agency": self.agency.pk, "deliverables": ["Trailer"],
                                                     "tier": 2, "deadline": dl, "editor": self.harsh.pk}, follow=True)
        job = Project.objects.get(couple="Plan Test")
        planned = PlanEntry.objects.filter(project=job, user=self.harsh)
        need = -(-S.hours_remaining(job) // 8)   # usual hours / 8 h, rounded up
        self.assertEqual(planned.count(), need)
        self.assertTrue(all(self.today <= e.date <= dl and S.is_workday_for(self.harsh, e.date) for e in planned))
        self.assertContains(r, "Planner")

    def test_existing_plans_are_not_overwritten_and_reassign_moves_plan(self):
        d = next_workday(self.today + timedelta(days=1), self.harsh)
        PlanEntry.objects.filter(user=self.harsh, date__gte=self.today).delete()
        PlanEntry.objects.create(user=self.harsh, date=d, label="Back-up")
        self.login("ag").post("/projects/new/", {"couple": "Keep Test", "agency": self.agency.pk, "deliverables": ["Teaser"],
                                                 "tier": 2, "deadline": self.today + timedelta(days=15), "editor": self.harsh.pk})
        self.assertEqual(PlanEntry.objects.get(user=self.harsh, date=d).label, "Back-up")
        job = Project.objects.get(couple="Keep Test")
        shabnam = U.objects.get(username="shabnam")
        self.login("ag").post(f"/projects/{job.pk}/assign/", {"editor": shabnam.pk})
        self.assertFalse(PlanEntry.objects.filter(project=job, user=self.harsh).exists())
        self.assertTrue(PlanEntry.objects.filter(project=job, user=shabnam).exists())

    def test_planner_copy_down_skips_days_off(self):
        d = next_workday(self.today + timedelta(days=1), self.harsh)
        self.login("ag").post("/planner/", {"user": self.harsh.pk, "date": d.isoformat(), "value": "Training", "repeat": "5"})
        days = list(PlanEntry.objects.filter(user=self.harsh, date__gt=d, label="Training").order_by("date"))
        self.assertEqual(len(days), 5)
        self.assertTrue(all(S.is_workday_for(self.harsh, e.date) for e in days))


class DeliveryDays(SeededTestCase):
    def test_delivery_days_marked_on_calendars(self):
        p = Project.objects.filter(editor__username="pratik", deadline__isnull=False).exclude(status="closed").first()
        m = p.deadline.strftime("%Y-%m")
        r = self.login("pratik").get(f"/my-month/?m={m}")
        from django.utils.html import escape
        self.assertContains(r, f"Due: {escape(str(p))}")
        self.assertContains(r, " due\"")
        r = self.login("ag").get(f"/deadlines/?m={m}")
        self.assertContains(r, " due\"")
        start = S.week_start(p.deadline)
        r = self.login("ag").get(f"/planner/?start={start}")
        self.assertContains(r, "due-tag")
        self.assertContains(r, "due-count")


class Delays(SeededTestCase):
    def setUp(self):
        self.p = Project.objects.filter(editor__username="pratik", deadline__isnull=False).exclude(status__in=("closed", "qc_passed")).first()
        self.old = self.p.deadline
        self.new = max(self.old, timezone.localdate()) + timedelta(days=4)

    def test_editor_reports_founder_approves(self):
        c = self.login("pratik")
        r = c.post(f"/my-jobs/{self.p.pk}/delay/", {"proposed_date": self.new, "reason": "client", "note": "New songs from client"})
        self.assertEqual(r.status_code, 302)
        d = DelayReport.objects.get(project=self.p)
        self.assertEqual((d.status, d.deadline_then), ("pending", self.old))
        self.assertContains(self.login("ag").get("/delays/"), "New songs from client")
        self.assertContains(self.login("ag").get("/dashboard/"), "delay request")
        self.login("ag").post(f"/delays/{d.pk}/decide/", {"decision": "approve", "approved_date": self.new, "founder_note": "OK"})
        self.p.refresh_from_db(); d.refresh_from_db()
        self.assertEqual((self.p.deadline, self.p.original_deadline, d.status), (self.new, self.old, "approved"))
        self.assertContains(c.get("/my-jobs/"), "New deadline approved")

    def test_decline_keeps_deadline(self):
        self.login("pratik").post(f"/my-jobs/{self.p.pk}/delay/", {"proposed_date": self.new, "reason": "workload", "note": "x"})
        d = DelayReport.objects.get(project=self.p)
        self.login("ag").post(f"/delays/{d.pk}/decide/", {"decision": "decline"})
        self.p.refresh_from_db()
        self.assertEqual(self.p.deadline, self.old)
        self.assertContains(self.login("pratik").get("/my-jobs/"), "Delay declined")

    def test_rules(self):
        c = self.login("pratik")
        today = timezone.localdate()
        self.p.deadline = today + timedelta(days=6); self.p.save()
        r = c.post(f"/my-jobs/{self.p.pk}/delay/", {"proposed_date": today + timedelta(days=2), "reason": "client", "note": "x"})
        self.assertContains(r, "after the current deadline")
        r = c.post(f"/my-jobs/{self.p.pk}/delay/", {"proposed_date": today - timedelta(days=1), "reason": "client", "note": "x"})
        self.assertContains(r, "today or a later date")
        other = Project.objects.exclude(editor__username="pratik").exclude(status="closed").filter(deadline__isnull=False).first()
        self.assertEqual(c.get(f"/my-jobs/{other.pk}/delay/").status_code, 404)
        self.assertEqual(c.get("/delays/").status_code, 403)

    def test_founder_records_delay_directly(self):
        self.login("ag").post(f"/projects/{self.p.pk}/delay/", {"proposed_date": self.new, "reason": "client", "note": "Agency asked"})
        self.p.refresh_from_db()
        self.assertEqual((self.p.deadline, self.p.original_deadline), (self.new, self.old))
        self.assertEqual(self.p.delays.get().status, "approved")


class ContractEditors(SeededTestCase):
    def _add(self, basis, rate):
        self.login("ag").post("/team/new/", {"first_name": "Kabir", "last_name": "Rao", "username": "Kabir_Rao", "role": "editor",
                                             "employment": "contract", "contract_basis": basis, "contract_rate": rate,
                                             "monthly_salary": 30000, "active": "on", "password": "Kite-Mango-4729"})
        return U.objects.get(username="Kabir_Rao")

    def test_contract_needs_basis(self):
        r = self.login("ag").post("/team/new/", {"first_name": "A", "username": "A_B", "role": "editor", "employment": "contract",
                                                 "monthly_salary": 0, "active": "on", "password": "Kite-Mango-4729"})
        self.assertContains(r, "Choose how this contract person is paid")

    def test_hourly_contractor_statement_and_payments(self):
        k = self._add("hour", 400)
        self.assertEqual((k.profile.employment, k.profile.monthly_salary), ("contract", 0))
        today = timezone.localdate()
        DayLog.objects.create(user=k, date=today, day_type="office", time_in=time(10), time_out=time(15))
        st = S.contract_statement(k, today)
        self.assertEqual(st["earned"], 2000)
        ag = self.login("ag")
        ag.post(f"/contract-editors/{k.pk}/", {"action": "pay", "m": today.strftime("%Y-%m"), "paid_on": today, "amount": 1500,
                                               "method": "upi", "reference": "UPI123"})
        self.assertEqual(ContractPayment.objects.get(user=k).amount, 1500)
        self.assertEqual(S.contract_statement(k, today)["balance"], 500)
        self.assertContains(ag.get("/contract-editors/"), "Kabir")
        self.assertNotIn(k, [r["u"] for r in ag.get("/payroll/").context["rows"]])   # not on salary payroll

    def test_per_film_fee_counts_when_delivered(self):
        k = self._add("project", 0)
        p = Project.objects.filter(status="closed").first()
        p.editor = k; p.save()
        ag = self.login("ag")
        r = ag.get(f"/contract-editors/{k.pk}/?m={p.closed_on:%Y-%m}")
        self.assertContains(r, "without a fee")
        ag.post(f"/contract-editors/{k.pk}/", {"action": "fee", "project": p.pk, "fee": "6000", "m": f"{p.closed_on:%Y-%m}"})
        self.assertEqual(S.contract_statement(k, p.closed_on)["earned"], 6000)

    def test_contractor_sees_own_payments_only(self):
        k = self._add("day", 1200)
        k.profile.must_change_password = False; k.profile.save()
        from django.test import Client
        c = Client(); c.login(username="kabir_rao", password="Kite-Mango-4729")
        self.assertEqual(c.get("/my-payments/").status_code, 200)
        self.assertEqual(c.get("/contract-editors/").status_code, 403)
        self.assertEqual(self.login("pratik").get("/my-payments/").status_code, 302)   # full-timers go to payslips


class CopyDown(SeededTestCase):
    def test_today_rows_have_copy_down(self):
        self.assertContains(self.login("pratik").get("/today/"), "data-copy-down")
