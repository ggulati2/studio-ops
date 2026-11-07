"""Fill an empty database with realistic demo data (your team, agencies,
films, logs, drives, invoices). Never run this on the real database."""
import random
from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ops.models import (Agency, AgencyRate, DayLog, EmployeeOfMonth, ExtraEntry, HardDrive, Holiday, Invoice,
                        InvoiceLine, PlanEntry, Pod, Profile, Project, Review, StudioSettings, TaskEntry)
from ops.services import add_months, month_start

User = get_user_model()


class Command(BaseCommand):
    help = "Load demo data. Refuses to run if projects already exist."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="demo-pass-123")

    def handle(self, *args, password, **opts):
        if Project.objects.exists():
            raise CommandError("Database already has projects; demo data is only for an empty database.")
        random.seed(7)
        today = timezone.localdate()
        s = StudioSettings.get()
        s.email, s.pan = "helloriggerfilms@gmail.com", "[PAN]"
        s.bank_account_no, s.bank_ifsc, s.bank_name = "[ACCOUNT NO.]", "[IFSC]", "[BANK]"
        s.save()

        def person(username, name, role, pod=None, salary=0):
            u = User.objects.create_user(username, password=password, first_name=name)
            Profile.objects.create(user=u, role=role, pod=pod, monthly_salary=salary, joined_on=date(2024, 4, 1))
            return u

        founder = User.objects.create_superuser("ag", password=password, first_name="AG")
        Profile.objects.create(user=founder, role=Profile.ADMIN)
        pods = [Pod.objects.create(name=f"Pod {i}") for i in (1, 2, 3)]
        nikhil = person("nikhil", "Nikhil", Profile.TL, pods[0], 45000)
        pratik = person("pratik", "Pratik", Profile.EDITOR, pods[0], 35000)
        soham = person("soham", "Soham", Profile.TL, pods[1], 45000)
        himanshu = person("himanshu", "Himanshu", Profile.EDITOR, pods[1], 35000)
        harsh = person("harsh", "Harsh", Profile.TL, pods[2], 45000)
        shabnam = person("shabnam", "Shabnam", Profile.EDITOR, pods[2], 35000)
        person("qc", "Ritika (QC)", Profile.QC, salary=30000)
        person("drives", "Drive desk", Profile.DRIVE, salary=20000)
        for p, lead in zip(pods, (nikhil, soham, harsh)):
            p.lead = lead
            p.save()
        editors = [nikhil, pratik, soham, himanshu, harsh, shabnam]

        ag = {}
        for name, city, cur, terms in [("BadalRaja Company", "Delhi", "INR", 15), ("Weddings By Shooting Star", "USA", "INR", 15),
                                       ("Elysia Films", "USA", "USD", 30), ("Happy Frames", "Mumbai", "INR", 15),
                                       ("Cheesecake Films", "Mumbai", "INR", 0), ("Qurbat Films", "", "INR", 15)]:
            a = Agency.objects.create(name=name, city=city, currency=cur, payment_terms_days=terms,
                                      contact_person="[CONTACT NAME]",
                                      billing_address="[ADDRESS]" if name != "Weddings By Shooting Star" else "")
            ag[name] = a
            for d, amt in (("Trailer", 18000), ("Full Film", 8000), ("Teaser", 9000), ("Highlight", 16000), ("Reels", 1500)):
                AgencyRate.objects.create(agency=a, deliverable=d, amount=amt if cur == "INR" else amt // 60)

        drives = {}
        for i, (a, contents) in enumerate([("BadalRaja Company", "Amit & Tanya wedding"), (None, "Diwali reels, influencer reel"),
                                           ("Happy Frames", "Gyan & Surbhi wedding"), ("Weddings By Shooting Star", "Liz & Chris wedding"),
                                           ("Elysia Films", "Swiss couple shoot"), ("Cheesecake Films", "Archana & Daniel wedding")], 1):
            code = f"{'SSD' if i in (4, 5) else 'HDD'}-{i:02d}"
            drives[code] = HardDrive.objects.create(code=code, kind=code[:3], agency=ag.get(a), studio_owned=a is None,
                                                    contents=contents, capacity_tb=4, data_tb=round(random.uniform(1, 3.5), 1),
                                                    received_on=today - timedelta(days=30 - i * 3), location=f"Rack {'ABC'[i % 3]} · {i}")

        def job(couple, deliv, agency, editor, deadline_days, status, drive, tier=2, amount=None, **kw):
            a = ag[agency]
            rate = {r.deliverable: r.amount for r in a.rates.all()}.get(deliv, 10000)
            return Project.objects.create(couple=couple, deliverable=deliv, agency=a, editor=editor, tier=tier,
                                          deadline=today + timedelta(days=deadline_days), status=status,
                                          drive=drives.get(drive), invoice_amount=amount or rate,
                                          received_on=today - timedelta(days=20), **kw)

        # delivered history so benchmarks exist
        hist = []
        for k in range(8):
            ed = editors[k % 6]
            p = job(f"Past Couple {k + 1}", ["Trailer", "Full Film", "Teaser", "Highlight"][k % 4], "BadalRaja Company", ed,
                    -25 + k, Project.CLOSED, "HDD-01", tier=2)
            p.closed_on = p.deadline - timedelta(days=random.choice([-1, 0, 1, 2]))
            p.project_file_link = "https://drive.google.com/drive/folders/demo"
            p.final_mp4_link = "https://drive.google.com/file/d/demo/view"
            p.save()
            hist.append(p)
        open_jobs = [
            job("Amit & Tanya", "Full Film", "BadalRaja Company", pratik, -3, Project.REVISIONS, "HDD-01", tier=1),
            job("Gyan & Surbhi", "Trailer", "Happy Frames", harsh, 2, Project.IN_PROGRESS, "HDD-03"),
            job("Diwali Reel", "Reels", "Qurbat Films", pratik, 1, Project.QC, "HDD-02", tier=1, is_extra=True,
                extra_basis="fixed", extra_rate=3000, extra_accepted=True, cut_link="https://drive.google.com/file/d/cut/view"),
            job("Liz & Chris", "Trailer", "Weddings By Shooting Star", nikhil, 11, Project.TL_REVIEW, "SSD-04", tier=3,
                cut_link="https://drive.google.com/file/d/cut2/view"),
            job("Liz & Chris", "Full Film", "Weddings By Shooting Star", himanshu, 12, Project.IN_PROGRESS, "SSD-04", tier=3),
            job("Swiss Couple", "Highlight", "Elysia Films", soham, 9, Project.QC_PASSED, "SSD-05", tier=3, amount=900),
            job("Archana & Daniel", "Trailer", "Cheesecake Films", shabnam, 14, Project.NOT_STARTED, "HDD-06"),
            job("Riya & Arjun", "Teaser", "BadalRaja Company", himanshu, 4, Project.IN_PROGRESS, "HDD-01"),
            job("Jashua & Sonali", "Trailer", "BadalRaja Company", None, 17, Project.NOT_STARTED, None),
            job("Yasaswi & Shehna", "Trailer", "Cheesecake Films", None, 19, Project.NOT_STARTED, "HDD-06"),
            job("Yasaswi & Shehna", "Reels", "Cheesecake Films", pratik, 16, Project.NOT_STARTED, "HDD-06", tier=1,
                is_extra=True, extra_basis="hourly", extra_rate=550),
        ]
        Review.objects.create(project=open_jobs[0], kind="qc", passed=False, issues=["Names, dates and titles spelled correctly"],
                              comments="00:12 Groom's name spelled wrong", reviewer=User.objects.get(username="qc"))

        hols = [Holiday.objects.create(date=today + timedelta(days=18), name="Studio holiday"),
                Holiday.objects.create(date=date(today.year, 12, 25), name="Christmas")]

        # 3 weeks of day logs
        workable = {e.id: [p for p in open_jobs + hist if p.editor_id == e.id] for e in editors}
        start = today - timedelta(days=21)
        for e in editors:
            d = start
            while d < today:
                if d.weekday() == 6:
                    d += timedelta(days=1)
                    continue
                r = random.random()
                kind = DayLog.LEAVE if r < 0.07 else DayLog.HALF if r < 0.1 else DayLog.WFH if r < 0.16 else DayLog.OFFICE
                if e == pratik and d in (today - timedelta(days=3), today - timedelta(days=9), today - timedelta(days=10)):
                    kind = DayLog.LEAVE
                if kind == DayLog.LEAVE:
                    DayLog.objects.create(user=e, date=d, day_type=kind)
                else:
                    tin = time(10, random.choice([0, 5, 10, 30]) if random.random() < .8 else 0) if random.random() < .85 else time(11, 15)
                    tout = time(14, 0) if kind == DayLog.HALF else time(random.choice([18, 19, 20]), 30)
                    log = DayLog.objects.create(user=e, date=d, day_type=kind, time_in=tin, time_out=tout)
                    pool = workable[e.id]
                    for p in random.sample(pool, min(len(pool), random.choice([1, 1, 2]))):
                        TaskEntry.objects.create(daylog=log, project=p, stage=random.choice(["Edit", "Color", "Selects", "Assembly"]))
                d += timedelta(days=1)
        ExtraEntry.objects.create(user=pratik, project=open_jobs[10], date=today - timedelta(days=2), start=time(21, 30), end=time(0, 30))
        ExtraEntry.objects.create(user=pratik, project=open_jobs[10], date=today - timedelta(days=4), start=time(22, 0), end=time(0, 30))

        for i, e in enumerate(editors):
            for k in range(10):
                d = today + timedelta(days=k)
                pool = workable[e.id]
                if pool and d.weekday() != 6:
                    PlanEntry.objects.create(user=e, date=d, project=[p for p in pool if p.status != Project.CLOSED][0]
                                             if any(p.status != Project.CLOSED for p in pool) else None, label="Back-up")

        inv = Invoice.objects.create(number=(today - timedelta(days=20)).strftime("%d%m%Y"), agency=ag["BadalRaja Company"],
                                     issued_on=today - timedelta(days=20), due_on=today - timedelta(days=5), status="sent")
        for p in hist[:4]:
            InvoiceLine.objects.create(invoice=inv, project=p, couple=p.couple, film=p.deliverable, amount=p.invoice_amount)
        inv2 = Invoice.objects.create(number=(today - timedelta(days=8)).strftime("%d%m%Y"), agency=ag["BadalRaja Company"],
                                      issued_on=today - timedelta(days=8), due_on=today + timedelta(days=7), status="part",
                                      received=10000, received_on=today - timedelta(days=2))
        for p in hist[4:6]:
            InvoiceLine.objects.create(invoice=inv2, project=p, couple=p.couple, film=p.deliverable, amount=p.invoice_amount)

        last = add_months(month_start(today), -1)
        EmployeeOfMonth.objects.create(month=last, user=shabnam, reason="Six films, zero QC fails, and she picked up the Diwali reels rush without being asked.",
                                       handed_over_on=month_start(today))
        EmployeeOfMonth.objects.create(month=add_months(last, -1), user=soham, reason="Delivered the Elysia destination wedding two days early.")
        EmployeeOfMonth.objects.create(month=add_months(last, -2), user=shabnam, reason="Highest client rating from BadalRaja this season.")
        # 3-letter nicknames shown at the end of every project name
        for name, code in {"BadalRaja Company": "BRC", "Weddings By Shooting Star": "WSS", "Elysia Films": "ELY",
                           "Happy Frames": "HFM", "Cheesecake Films": "CCF", "Qurbat Films": "QBT",
                           "Shutter Magik Films": "SMF", "Dream Diaries": "DRD"}.items():
            Agency.objects.filter(name=name).update(code=code)
        # One delivered film back with the editor for client changes
        back = Project.objects.filter(status=Project.CLOSED, editor=harsh).order_by("-closed_on").first()
        if back:
            from ops.models import ClientRevision
            ClientRevision.objects.create(project=back, changes="Client wants the haldi section shorter and a new end song.",
                                          due_date=today + timedelta(days=3), editor=harsh, round=1)
            back.status, back.deadline = Project.CLIENT_REV, today + timedelta(days=3)
            back.save(update_fields=["status", "deadline"])
        # A contract editor paid per film, with one delivered film and a part payment
        from ops.models import ContractPayment, DelayReport
        kabir = person("kabir", "Kabir", Profile.EDITOR, pods[2], 0)
        kabir.last_name = "Rao"; kabir.save()
        kabir.profile.employment, kabir.profile.contract_basis = Profile.CONTRACT, "project"
        kabir.profile.save()
        done = Project.objects.create(couple="Meera & Dev", deliverable="Teaser", agency=ag["Happy Frames"], tier=2, editor=kabir,
                                      deadline=today - timedelta(days=6), status=Project.CLOSED, closed_on=today - timedelta(days=7),
                                      invoice_amount=9000, contractor_fee=4000)
        Project.objects.create(couple="Sana & Imran", deliverable="Highlight", agency=ag["Happy Frames"], tier=2, editor=kabir,
                               deadline=today + timedelta(days=12), status=Project.IN_PROGRESS, invoice_amount=16000, contractor_fee=7000)
        ContractPayment.objects.create(user=kabir, paid_on=today - timedelta(days=2), amount=2500, method="upi", reference="UPI 4471")
        # Agency deadlines a few days after the editors' deadlines; one film that came online
        for p in Project.objects.exclude(status=Project.CLOSED).filter(deadline__isnull=False):
            p.agency_deadline = p.deadline + timedelta(days=4)
            p.save(update_fields=["agency_deadline"])
        Project.objects.filter(couple="Sana & Imran").update(received_online=True, drive=None,
                                                             cloud_link="https://wetransfer.com/downloads/demo")
        # Pratik works on revisions to a delivered film alongside his main job for two days
        old = Project.objects.filter(status=Project.CLOSED).order_by("-closed_on").first()
        for k in (1, 2):
            d = today + timedelta(days=k)
            if PlanEntry.objects.filter(user=pratik, date=d, slot=1).exists() and old:
                PlanEntry.objects.get_or_create(user=pratik, date=d, slot=2, defaults={"project": old, "label": "Revisions"})
        # A delay waiting for a founder's decision
        late = Project.objects.filter(editor=himanshu).exclude(status=Project.CLOSED).filter(deadline__isnull=False).first()
        if late:
            DelayReport.objects.create(project=late, reported_by=himanshu, deadline_then=late.deadline,
                                       proposed_date=late.deadline + timedelta(days=3), reason="client",
                                       note="Agency sent a new song list yesterday; the trailer needs a re-cut.")
        self.stdout.write(self.style.SUCCESS(f"Demo data loaded. Log in as ag / nikhil / pratik / kabir / qc / drives with password '{password}'."))
