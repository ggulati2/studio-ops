"""The studio's real workflows and money rules, end to end."""
import calendar
import io
from datetime import time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from ops import services as S
from ops.models import (Agency, Congrats, DayLog, HardDrive, Holiday, Invoice, PayrollMonth, Payslip, PlanEntry,
                        Project)

from .base import SeededTestCase

U = get_user_model()


def jpeg(w=3000, h=2000, name="d.jpg"):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "red").save(buf, "JPEG")
    buf.seek(0)
    buf.name = name
    return buf


class Flows(SeededTestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.pratik, self.nikhil = U.objects.get(username="pratik"), U.objects.get(username="nikhil")
        self.agency = Agency.objects.get(name="BadalRaja Company")

    def test_day_log_splits_hours(self):
        c = self.login("pratik")
        d = self.today + timedelta(days=1 if self.today.weekday() != 5 else 2)
        open_ = Project.objects.filter(editor=self.pratik).exclude(status="closed")
        p1, p2 = open_[0], open_[1]
        mgmt = {"form-TOTAL_FORMS": "4", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "6"}
        r = c.post(f"/today/?date={d}", {"day_type": "office", "time_in": "10:00", "time_out": "19:30", "notes": "", **mgmt,
                                         "form-0-project": p1.pk, "form-0-stage": "Edit", "form-0-hours": "6",
                                         "form-1-project": p2.pk, "form-1-stage": "Color", "form-1-hours": ""})
        log = DayLog.objects.get(user=self.pratik, date=d)
        self.assertEqual(r.status_code, 302)
        self.assertEqual((log.office_hours, log.hours), (9.5, 8.5))      # 1-hour break taken off
        self.assertEqual(sorted(S.split_hours(log).values()), [2.5, 6.0])
        r = c.post(f"/today/?date={d}", {"day_type": "office", "time_in": "10:00", "time_out": "19:30", **mgmt,
                                         "form-TOTAL_FORMS": "1", "form-0-project": p1.pk, "form-0-stage": "", "form-0-hours": ""})
        self.assertContains(r, "Pick a stage")
        self.assertEqual(DayLog(user=self.pratik, date=d, time_in=time(21), time_out=time(1, 30)).hours, 4.5)   # short day: no break
        self.assertEqual(DayLog(user=self.pratik, date=d, time_in=time(9), time_out=time(18)).hours, 8.0)       # 9 h office = 8 h

    def test_review_qc_and_close(self):
        job = Project.objects.get(couple="Riya & Arjun", deliverable="Teaser")   # Himanshu, Soham's pod
        self.login("himanshu").post(f"/my-jobs/{job.pk}/submit/", {"cut_link": "https://drive.google.com/file/d/x/view"})
        job.refresh_from_db(); self.assertEqual(job.status, "tl_review")
        self.assertEqual(self.login("nikhil").post(f"/review/{job.pk}/", {"decision": "pass"}).status_code, 403)
        self.login("soham").post(f"/review/{job.pk}/", {"decision": "pass", "comments": ""})
        job.refresh_from_db(); self.assertEqual(job.status, "qc")
        qc = self.login("qc")
        qc.post(f"/qc/{job.pk}/", {"decision": "fail", "issue": ["3"], "comments": "00:12 name typo"})
        job.refresh_from_db(); self.assertEqual(job.status, "revisions")
        self.assertTrue(job.reviews.filter(kind="qc", passed=False).exists())
        job.status = "qc"; job.save()
        qc.post(f"/qc/{job.pk}/", {"decision": "pass"})
        job.refresh_from_db(); self.assertEqual(job.status, "qc_passed")
        c = self.login("himanshu")
        r = c.post(f"/my-jobs/{job.pk}/close/", {"project_file_link": "https://dropbox.com/x",
                                                 "final_mp4_link": "https://drive.google.com/file/d/y/view", "confirm": "on"})
        self.assertContains(r, "company Drive")
        c.post(f"/my-jobs/{job.pk}/close/", {"project_file_link": "https://drive.google.com/drive/folders/p",
                                             "final_mp4_link": "https://drive.google.com/file/d/y/view", "confirm": "on"})
        job.refresh_from_db(); self.assertEqual(job.status, "closed"); self.assertEqual(job.closed_on, self.today)
        # Any stage can be closed by its editor: it goes straight to Delivered
        early = Project.objects.filter(status="not_started", editor=self.pratik).first()
        pc = self.login("pratik")
        self.assertContains(pc.get("/my-jobs/"), f"/my-jobs/{early.pk}/close/")
        self.assertContains(pc.get(f"/my-jobs/{early.pk}/close/"), "You can still close it")
        pc.post(f"/my-jobs/{early.pk}/close/", {"project_file_link": "https://drive.google.com/a",
                                                "final_mp4_link": "https://drive.google.com/b", "confirm": "on"})
        early.refresh_from_db(); self.assertEqual(early.status, "closed"); self.assertEqual(early.closed_on, self.today)
        self.assertEqual(early.final_mp4_link, "https://drive.google.com/b")
        # Links and the tick box are still required; someone else's job or a closed one can't be touched
        other = Project.objects.filter(editor=self.pratik).exclude(status="closed").first()
        r = pc.post(f"/my-jobs/{other.pk}/close/", {"project_file_link": "", "final_mp4_link": ""})
        other.refresh_from_db(); self.assertNotEqual(other.status, "closed")
        self.assertEqual(pc.get(f"/my-jobs/{job.pk}/close/").status_code, 404)
        self.assertEqual(pc.get(f"/my-jobs/{early.pk}/close/").status_code, 404)

    def test_invoices_and_payments(self):
        ca = self.login("ag")
        r = ca.get(f"/invoices/new/?agency={self.agency.pk}")
        cands = [p for p in r.context["candidates"] if p.status == "closed"]
        ca.post("/invoices/new/", {"agency": self.agency.pk, "number": "TEST-1", "pid": [p.pk for p in cands],
                                   **{f"amount_{p.pk}": p.invoice_amount for p in cands},
                                   "x_couple_0": "Liz and Chris", "x_film_0": "Cleanup Shots", "x_amount_0": "5000", "send": "1"})
        inv = Invoice.objects.get(number="TEST-1")
        exp = sum(p.invoice_amount for p in cands) + 5000
        self.assertEqual(inv.total, exp); self.assertEqual(inv.status, "sent")
        ca.post("/invoices/new/", {"agency": self.agency.pk, "number": "TEST-1", "x_couple_0": "a", "x_film_0": "b", "x_amount_0": "1"})
        self.assertEqual(Invoice.objects.filter(number="TEST-1").count(), 1)
        ca.post(f"/invoices/{inv.pk}/update/", {"status": "sent", "received": "5000"})
        inv.refresh_from_db(); self.assertEqual(inv.status, "part"); self.assertEqual(inv.balance, exp - 5000)
        ca.post(f"/invoices/{inv.pk}/update/", {"status": "paid", "received": "0"})
        inv.refresh_from_db(); self.assertEqual(inv.status, "paid"); self.assertEqual(inv.balance, 0)
        old = Invoice.objects.filter(due_on__lt=self.today, status="sent").first()
        ca.get("/invoices/")
        if old:
            old.refresh_from_db(); self.assertEqual(old.status, "overdue")
        r = ca.get(f"/invoices/{inv.pk}/")
        self.assertContains(r, "Cleanup Shots"); self.assertContains(r, "rigger-white")

    def test_payroll_leave_deduction(self):
        m = S.month_start(self.today)
        DayLog.objects.filter(user=self.pratik, date__range=(m, S.month_end(m))).delete()
        for k in range(5):
            DayLog.objects.create(user=self.pratik, date=m + timedelta(days=k), day_type="leave")
        DayLog.objects.create(user=self.pratik, date=m + timedelta(days=6), day_type="half", time_in=time(10), time_out=time(14))
        Holiday.objects.filter(date__range=(m, m + timedelta(days=6))).delete()
        f = S.payslip_figures(self.pratik, m)
        days = calendar.monthrange(m.year, m.month)[1]
        ded = round(Decimal("3.5") * Decimal(35000) / days)
        self.assertEqual(f["leaves_taken"], Decimal("5.5"))
        self.assertEqual(f["unpaid_leaves"], Decimal("3.5"))
        self.assertEqual(f["leave_deduction"], ded)
        self.assertEqual(f["net"], 35000 - ded + f["extra_pay"])
        self.login("ag").post("/payroll/save/", {"m": m.strftime("%Y-%m"), f"other_{self.pratik.pk}": "500",
                                                 f"note_{self.pratik.pk}": "travel", "finalize": "1"})
        slip = Payslip.objects.get(payroll__month=m, user=self.pratik)
        self.assertEqual(slip.net, f["net"] + 500)
        self.assertTrue(PayrollMonth.objects.get(month=m).finalized)
        self.assertEqual(self.login("pratik").get(f"/payslip/{slip.pk}/").status_code, 200)
        self.assertEqual(self.login("himanshu").get(f"/payslip/{slip.pk}/").status_code, 403)
        self.assertEqual(self.login("qc").get(f"/payslip/{slip.pk}/").status_code, 403)

    def test_extra_work_pays_by_the_hour(self):
        c = self.login("pratik")
        xj = Project.objects.get(couple="Yasaswi & Shehna", deliverable="Reels")
        c.post(f"/my-extra/{xj.pk}/respond/", {"answer": "yes"})
        xj.refresh_from_db(); self.assertTrue(xj.extra_accepted)
        before = S.extra_pay(self.pratik, self.today)
        c.post("/my-extra/", {"project": xj.pk, "stage": "Edit", "date": self.today, "start": "21:00", "end": "23:30", "note": ""})
        self.assertEqual(S.extra_pay(self.pratik, self.today) - before, round(2.5 * 550))

    def test_planner_and_holidays(self):
        ca = self.login("ag")
        d = self.today + timedelta(days=3)
        ca.post("/planner/", {"user": self.nikhil.pk, "date": d.isoformat(), "value": "Back-up"})
        self.assertEqual(PlanEntry.objects.get(user=self.nikhil, date=d).label, "Back-up")
        ca.post("/settings/", {"add_holiday": "1", "h-date": d.isoformat(), "h-name": "Test Holiday", "h-kind": "full"})
        self.assertTrue(Holiday.objects.filter(name="Test Holiday").exists())
        self.assertEqual(S.day_kind(self.nikhil, d), "holiday")
        ws = S.week_start(d)
        self.assertLessEqual(S.available_days(self.nikhil, ws, ws + timedelta(days=6)), 5)

    def test_employee_of_the_month(self):
        m = S.month_start(self.today)
        self.login("ag").post("/trailblazer-of-the-month/", {"month": S.add_months(m, -1).strftime("%Y-%m"), "user": self.nikhil.pk,
                                                          "reason": "Great month", "voucher_amount": 1000})
        self.assertEqual(S.celebrated_award().user_id, self.nikhil.pk)
        r = self.login("nikhil").get("/today/")
        self.assertContains(r, "Congratulations, Nikhil"); self.assertContains(r, "celebrate")
        self.assertNotContains(r, "sent to your email")
        p = self.login("pratik")
        self.assertContains(p.get("/today/"), "Say congrats")
        p.post("/congrats/", {"next": "/today/"})
        self.assertTrue(Congrats.objects.filter(user=self.pratik).exists())

    def test_drive_photos_and_send_back(self):
        cd = self.login("drives")
        cd.post("/drives/", {"code": "HDD-99", "kind": "HDD", "contents": "Test", "received_on": self.today,
                             "received_via": "courier", "status": "in_use",
                             "photos_upload": [jpeg(), jpeg(800, 600, "b.jpg")]})
        dr = HardDrive.objects.get(code="HDD-99")
        self.assertEqual(dr.photos.count(), 2)
        ph = dr.photos.first()
        self.assertLess(len(ph.data), 400_000)
        self.assertEqual(cd.get(f"/img/{ph.pk}/").status_code, 200)
        dr.status = "ready"; dr.save()
        cd.post(f"/drives/send-back/?d={dr.pk}", {"sent_on": self.today, "sent_to": "Agency", "method": "courier", "courier": "X",
                                                  "tracking": "1", "condition_ok": "on", "status": "transit"})
        dr.refresh_from_db(); self.assertEqual(dr.status, "sent"); self.assertTrue(dr.returns.exists())
        busy = HardDrive.objects.get(code="HDD-06"); busy.status = "ready"; busy.save()
        cd.post(f"/drives/send-back/?d={busy.pk}", {"sent_on": self.today, "sent_to": "A", "method": "courier",
                                                    "status": "transit", "condition_ok": "on"})
        busy.refresh_from_db(); self.assertNotEqual(busy.status, "sent")

    def test_client_report_overrides_and_hides_money(self):
        ca = self.login("ag")
        p = Project.objects.filter(agency=self.agency).exclude(status="closed").first()
        later = self.today + timedelta(days=30)
        ca.post(f"/reports/?agency={self.agency.pk}", {"pid": [p.pk], f"stage_{p.pk}": "Editing", f"date_{p.pk}": later.isoformat()})
        p.refresh_from_db()
        r = ca.get(f"/reports/print/?agency={self.agency.pk}")
        self.assertEqual(p.client_stage, "Editing")
        self.assertContains(r, later.strftime("%-d %b"))
        self.assertNotContains(r, "₹")

    def test_new_project_makes_one_job_per_deliverable(self):
        self.login("ag").post("/projects/new/", {"couple": "Test Couple", "agency": self.agency.pk, "deliverables": ["Trailer", "Teaser"],
                                                 "deadline": self.today + timedelta(days=20)})
        made = Project.objects.filter(couple="Test Couple")
        self.assertEqual(made.count(), 2)
        self.assertEqual(made.get(deliverable="Trailer").invoice_amount, 18000)
