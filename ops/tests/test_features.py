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
        self.login("ag").post("/planner/", {"user": self.harsh.pk, "date": d.isoformat(), "value": "Training", "repeat": "5", "repeat_go": "1"})
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


class PlannerTwoThings(SeededTestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.pratik = U.objects.get(username="pratik")
        self.d = next_workday(self.today + timedelta(days=5), self.pratik)
        PlanEntry.objects.filter(user=self.pratik, date=self.d).delete()

    def test_second_slot_and_revisions(self):
        ag = self.login("ag")
        job = Project.objects.filter(editor=self.pratik).exclude(status="closed").first()
        delivered = Project.objects.filter(status="closed").first()
        ag.post("/planner/", {"user": self.pratik.pk, "date": self.d, "value": f"p:{job.pk}", "slot": "1"})
        ag.post("/planner/", {"user": self.pratik.pk, "date": self.d, "value": f"r:{delivered.pk}", "slot": "2"})
        cell = list(PlanEntry.objects.filter(user=self.pratik, date=self.d))
        self.assertEqual([(e.slot, e.text) for e in cell], [(1, str(job)), (2, f"Revisions: {delivered}")])
        r = self.login("pratik").get(f"/today/?date={self.d}")
        self.assertContains(r, "2 things today")
        self.assertContains(r, "alongside")
        self.assertContains(r, f'value="{delivered.pk}"')               # delivered film is pickable for logging
        # clearing the main slot promotes the second
        ag.post("/planner/", {"user": self.pratik.pk, "date": self.d, "value": "", "slot": "1"})
        self.assertEqual([(e.slot, e.is_revision) for e in PlanEntry.objects.filter(user=self.pratik, date=self.d)], [(1, True)])

    def test_select_change_does_not_copy_down(self):
        ag = self.login("ag")
        ag.post("/planner/", {"user": self.pratik.pk, "date": self.d, "value": "Training", "slot": "1", "repeat": "3"})
        self.assertFalse(PlanEntry.objects.filter(user=self.pratik, date__gt=self.d, label="Training").exists())

    def test_auto_plan_goes_alongside_when_days_are_full(self):
        from ops.models import Agency
        harsh = U.objects.get(username="harsh")
        dl = next_workday(self.today + timedelta(days=2), harsh)
        d = self.today
        while d <= dl:                                     # fill every day with other work
            PlanEntry.objects.update_or_create(user=harsh, date=d, slot=1, defaults={"label": "Back-up", "project": None})
            d += timedelta(days=1)
        self.login("ag").post("/projects/new/", {"couple": "Busy Test", "agency": Agency.objects.first().pk,
                                                 "deliverables": ["Reels"], "tier": 1, "deadline": dl, "editor": harsh.pk})
        job = Project.objects.get(couple="Busy Test")
        self.assertTrue(PlanEntry.objects.filter(project=job, slot=2).exists())
        self.assertFalse(PlanEntry.objects.filter(project=job, slot=1).exists())


class NewProjectOptions(SeededTestCase):
    def setUp(self):
        from ops.models import Agency
        self.today = timezone.localdate()
        self.agency = Agency.objects.get(name="BadalRaja Company")

    def test_per_film_dates_and_amounts(self):
        from ops.forms import film_key
        t, f = film_key("Trailer"), film_key("Full Film")
        base = self.today + timedelta(days=10)
        r = self.login("ag").post("/projects/new/", {
            "couple": "Two Films", "agency": self.agency.pk, "deliverables": ["Trailer", "Full Film"], "tier": 2,
            "deadline": base, "agency_deadline": base + timedelta(days=3),
            f"f{f}_deadline": base + timedelta(days=12), f"f{f}_agency_deadline": base + timedelta(days=16), f"f{f}_amount": 52000})
        self.assertEqual(r.status_code, 302)
        tr, ff = Project.objects.get(couple="Two Films", deliverable="Trailer"), Project.objects.get(couple="Two Films", deliverable="Full Film")
        self.assertEqual((tr.deadline, tr.agency_deadline, tr.invoice_amount), (base, base + timedelta(days=3), 18000))
        self.assertEqual((ff.deadline, ff.agency_deadline, ff.invoice_amount), (base + timedelta(days=12), base + timedelta(days=16), 52000))

    def test_editor_deadline_must_not_pass_agency_deadline(self):
        r = self.login("ag").post("/projects/new/", {"couple": "Bad Dates", "agency": self.agency.pk, "deliverables": ["Teaser"],
                                                     "tier": 2, "deadline": self.today + timedelta(days=9),
                                                     "agency_deadline": self.today + timedelta(days=5)})
        self.assertContains(r, "is after the agency")
        self.assertFalse(Project.objects.filter(couple="Bad Dates").exists())

    def test_cloud_source(self):
        self.login("ag").post("/projects/new/", {"couple": "Online Couple", "agency": self.agency.pk, "deliverables": ["Teaser"],
                                                 "tier": 2, "source": "cloud", "cloud_link": "https://wetransfer.com/x",
                                                 "editor": U.objects.get(username="pratik").pk})
        p = Project.objects.get(couple="Online Couple")
        self.assertEqual((p.received_online, p.drive_id, p.source_label), (True, None, "Cloud"))
        self.assertContains(self.login("pratik").get("/my-jobs/"), "https://wetransfer.com/x")
        self.assertContains(self.login("ag").get("/projects/new/"), "Cloud / online")

    def test_agency_deadline_never_shown_to_editors(self):
        p = Project.objects.filter(editor__username="pratik").exclude(status="closed").filter(deadline__isnull=False).first()
        secret = self.today + timedelta(days=200)
        p.agency_deadline = secret; p.save()
        for url in ("/my-jobs/", "/today/", f"/my-month/?m={secret:%Y-%m}", f"/my-jobs/{p.pk}/delay/"):
            with self.subTest(url=url):
                body = self.login("pratik").get(url).content.decode()
                self.assertNotIn(secret.strftime("%-d %b"), body)
                self.assertNotIn("agency's deadline", body.lower().replace("&#x27;", "'"))
                if "my-month" in url:      # the calendar links every day, but must not mark this one as due
                    self.assertNotIn(" due\"", body)
                else:
                    self.assertNotIn(secret.isoformat(), body)
        nikhil_pod = self.login("nikhil").get("/pod/").content.decode()
        self.assertNotIn(secret.strftime("%-d %b"), nikhil_pod)
        self.assertContains(self.login("ag").get("/projects/?status="), secret.strftime("%-d %b"))


class RevisionsAndTags(SeededTestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.p = Project.objects.filter(status="closed", editor__username="pratik").first()
        self.p.deadline = self.p.closed_on            # delivered exactly on time
        self.p.first_delivered_on = self.p.closed_on
        self.p.deadline_at_delivery = self.p.closed_on
        self.p.save()

    def test_statuses(self):
        names = dict(Project.STATUSES)
        self.assertEqual((names["tl_revisions"], names["revisions"], names["client_rev"]),
                         ("TL revisions", "QC revisions", "Client revision"))

    def test_tl_sends_back_to_tl_revisions_and_editor_can_resubmit(self):
        job = Project.objects.get(couple="Riya & Arjun", deliverable="Teaser")
        job.status = "tl_review"; job.save()
        self.login("soham").post(f"/review/{job.pk}/", {"decision": "fail", "comments": "Tighten the intro"})
        job.refresh_from_db(); self.assertEqual(job.status, "tl_revisions")
        self.login("himanshu").post(f"/my-jobs/{job.pk}/submit/", {"cut_link": "https://drive.google.com/x"})
        job.refresh_from_db(); self.assertEqual(job.status, "tl_review")

    def test_client_revision_reopens_without_making_it_late(self):
        first = self.p.closed_on
        due = self.today + timedelta(days=4)
        r = self.login("ag").post(f"/projects/{self.p.pk}/client-revision/", {
            "requested_on": self.today, "changes": "New end song", "due_date": due, "editor": self.p.editor_id})
        self.assertEqual(r.status_code, 302)
        self.p.refresh_from_db()
        self.assertEqual((self.p.status, self.p.deadline, self.p.closed_on), ("client_rev", due, first))
        self.assertIn(self.p, [j["p"] for j in self.login("pratik").get("/my-jobs/").context["jobs"]])
        # back through QC and closed again, weeks after the original deadline
        self.p.status = "qc_passed"; self.p.save()
        self.login("pratik").post(f"/my-jobs/{self.p.pk}/close/", {"project_file_link": "https://drive.google.com/drive/folders/a",
                                                                   "final_mp4_link": "https://drive.google.com/file/d/b", "confirm": "on"})
        self.p.refresh_from_db()
        self.assertEqual((self.p.status, self.p.closed_on, self.p.first_delivered_on), ("closed", first, first))
        self.assertFalse(self.p.delivered_late)
        self.assertEqual(self.p.timing_label, "Delivered on time")
        self.assertIsNotNone(self.p.client_revisions.get().completed_on)

    def test_delivery_tag_can_be_changed(self):
        self.p.deadline_at_delivery = self.p.first_delivered_on - timedelta(days=3)   # really late by dates
        self.p.save()
        self.assertTrue(self.p.delivered_late)
        m = self.p.closed_on.strftime("%Y-%m")
        self.login("ag").post(f"/delivered/?m={m}", {"project": self.p.pk, "timing_override": "client_delay",
                                                     "timing_note": "Waited 4 days for song approval"})
        self.p.refresh_from_db()
        self.assertFalse(self.p.delivered_late)
        self.assertEqual(self.p.timing_label, "Late – client approval")
        self.assertContains(self.login("ag").get(f"/delivered/?m={m}"), "Waited 4 days for song approval")


class AgencyNickname(SeededTestCase):
    def test_code_added_to_project_names(self):
        from ops.models import Agency
        a = Agency.objects.get(name="BadalRaja Company")
        r = self.login("ag").post(f"/agencies/{a.pk}/", {"name": a.name, "code": "brc", "currency": "INR",
                                                         "payment_terms_days": 15, "active": "on", "rates": "Trailer = 18000"})
        self.assertEqual(r.status_code, 302)
        a.refresh_from_db(); self.assertEqual(a.code, "BRC")
        p = Project.objects.filter(agency=a).first()
        self.assertTrue(str(p).endswith(f"{p.deliverable} – BRC"))
        b = Agency.objects.exclude(pk=a.pk).first()
        r = self.login("ag").post(f"/agencies/{b.pk}/", {"name": b.name, "code": "BRC", "currency": "INR",
                                                         "payment_terms_days": 15, "active": "on"})
        self.assertContains(r, "already used")
        r = self.login("ag").post(f"/agencies/{b.pk}/", {"name": b.name, "code": "AB1", "currency": "INR",
                                                         "payment_terms_days": 15, "active": "on"})
        self.assertContains(r, "1 to 3 letters")
