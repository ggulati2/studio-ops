"""Projects grouped as Agency → Couple → Films."""
import json
from datetime import timedelta
from importlib import import_module

from django.apps import apps as django_apps
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ops import services as S
from ops.models import Agency, AuditEvent, Couple, Project, couple_key

from .base import SeededTestCase

FETCH = {"HTTP_X_REQUESTED_WITH": "fetch"}


class CoupleRecords(SeededTestCase):
    def setUp(self):
        self.ag = Agency.objects.get(code="BRC")

    def test_key_ignores_spelling(self):
        self.assertEqual(couple_key("Amit & Tanya"), couple_key("  amit and TANYA "))
        self.assertEqual(couple_key("Amit + Tanya"), couple_key("Amit weds Tanya"))
        self.assertNotEqual(couple_key("Amit & Tanya"), couple_key("Amit & Tara"))

    def test_films_link_to_one_couple_on_save(self):
        a = Project.objects.create(couple="Neha & Karan", deliverable="Teaser", agency=self.ag)
        b = Project.objects.create(couple="neha and karan", deliverable="Trailer", agency=self.ag)
        self.assertIsNotNone(a.client_id)
        self.assertEqual(a.client_id, b.client_id)
        # same names under another agency are a different couple
        other = Project.objects.create(couple="Neha & Karan", deliverable="Teaser", agency=Agency.objects.get(code="HFM"))
        self.assertNotEqual(other.client_id, a.client_id)
        # fixing a typo in the film moves it to the right couple
        b.couple = "Neha & Kiran"
        b.save()
        self.assertNotEqual(b.client_id, a.client_id)

    def test_every_seeded_film_has_a_couple(self):
        self.assertFalse(Project.objects.filter(client__isnull=True).exists())

    def test_migration_groups_existing_films(self):
        """The 0013 data migration puts films already in the portal under couples: no re-entry."""
        mig = import_module("ops.migrations.0013_couples")
        Project.objects.update(client=None)
        Couple.objects.all().delete()
        Project.objects.create(couple="Riya & Arjun", deliverable="Reels", agency=self.ag)   # links itself
        Project.objects.update(client=None)
        Couple.objects.all().delete()
        mig.group_existing_films(django_apps, None)
        self.assertFalse(Project.objects.filter(client__isnull=True).exists())
        riya = Project.objects.filter(couple__iexact="riya & arjun", agency=self.ag)
        self.assertGreaterEqual(riya.count(), 2)
        self.assertEqual(riya.values("client").distinct().count(), 1)


class _WithCouple(SeededTestCase):
    def setUp(self):
        self.f = self.login("ag")
        self.ag = Agency.objects.get(code="BRC")
        self.c = Couple.for_name(self.ag, "Meera & Dev")
        today = timezone.localdate()
        self.films = [Project.objects.create(couple="Meera & Dev", deliverable=d, agency=self.ag,
                                             deadline=today + timedelta(days=i + 3),
                                             agency_deadline=today + timedelta(days=i + 10))
                      for i, d in enumerate(["Teaser", "Trailer", "Full Film", "Reels"])]


class GroupedView(_WithCouple):

    def test_grouped_is_default_and_lists_couples(self):
        r = self.f.get("/projects/")
        self.assertContains(r, 'data-groups')
        self.assertContains(r, f'data-couple="{self.c.pk}"')
        self.assertContains(r, "Meera &amp; Dev")
        self.assertContains(r, "0/4")
        self.assertNotIn("jobs", r.context)
        # the list is one click away and still works
        r = self.f.get("/projects/?view=list")
        self.assertIn("jobs", r.context)

    def test_search_opens_couples_inline(self):
        r = self.f.get("/projects/?q=Meera")
        self.assertContains(r, f'action="/projects/couple/{self.c.pk}/action/"')
        self.assertContains(r, 'name="layout" value="couple"')

    def test_couple_block_loads_with_filters(self):
        Project.objects.filter(pk=self.films[0].pk).update(status=Project.CLOSED)
        r = self.f.get(f"/projects/couple/{self.c.pk}/?status=open")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertEqual(html.count('<tr data-pk="'), 3)
        self.assertIn("1 more film for Meera &amp; Dev is hidden by the filters", html)

    def test_page_cost_does_not_grow_with_couples(self):
        def count():
            self.f.get("/projects/?status=")
            with CaptureQueriesContext(connection) as q:
                self.f.get("/projects/?status=")
            return len(q.captured_queries)
        before = count()
        for i in range(25):
            Project.objects.create(couple=f"Load Couple {i}", deliverable="Teaser", agency=self.ag)
        self.assertLessEqual(count(), before + 1)

    def test_editors_cannot_open_couples(self):
        e = self.login("pratik")
        self.assertNotEqual(e.get(f"/projects/couple/{self.c.pk}/").status_code, 200)
        r = e.post(f"/projects/couple/{self.c.pk}/action/", {"op": "status", "status": "closed"})
        self.assertNotEqual(r.status_code, 200)
        self.assertFalse(Project.objects.filter(client=self.c, status=Project.CLOSED).exists())

    def test_row_edit_inside_couple_refreshes_summaries(self):
        p = self.films[0]
        r = self.f.post(f"/projects/{p.pk}/status/", {"status": "closed", "layout": "couple",
                                                      "next": "/projects/?status="}, **FETCH)
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertIn('data-col="film"', d["row"])
        self.assertNotIn('data-col="agency"', d["row"])
        sels = [s["sel"] for s in d["swap"]]
        self.assertIn(f'[data-couple-sum="{self.c.pk}"]', sels)
        self.assertIn(f'[data-agency-sum="{self.ag.pk}"]', sels)
        self.assertIn("1/4", next(s["html"] for s in d["swap"] if "couple-sum" in s["sel"]))
        self.assertEqual(d["undo"]["layout"], "couple")


class CoupleActions(_WithCouple):
    def act(self, data, ids=None):
        payload = {"qs": "status=", **data}
        if ids is not None:
            payload["ids"] = [str(i) for i in ids]
        r = self.f.post(f"/projects/couple/{self.c.pk}/action/", payload, **FETCH)
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_assign_all_open_films(self):
        from django.contrib.auth import get_user_model
        pratik = get_user_model().objects.get(username="pratik")
        Project.objects.filter(pk=self.films[3].pk).update(status=Project.CLOSED)
        d = self.act({"op": "assign", "editor": pratik.pk})
        self.assertTrue(d["ok"])
        self.assertEqual(Project.objects.filter(client=self.c, editor=pratik).count(), 3)   # delivered one untouched
        self.assertTrue(any(s["sel"] == f'[data-couple="{self.c.pk}"]' and s["mode"] == "outer" for s in d["swap"]))
        self.assertTrue(AuditEvent.objects.filter(action="couple.assign").exists())

    def test_status_only_for_films_shown(self):
        d = self.act({"op": "status", "status": "qc"}, ids=[self.films[0].pk, self.films[1].pk])
        self.assertTrue(d["ok"])
        self.assertEqual(Project.objects.filter(client=self.c, status="qc").count(), 2)
        # ids from another couple are ignored
        stranger = Project.objects.exclude(client=self.c).first()
        self.act({"op": "status", "status": "on_hold"}, ids=[stranger.pk])
        stranger.refresh_from_db()
        self.assertNotEqual(stranger.status, "on_hold")

    def test_shift_deadlines(self):
        before = {p.pk: (p.deadline, p.agency_deadline) for p in self.films}
        d = self.act({"op": "shift", "days": "3", "which": "both"})
        self.assertTrue(d["ok"])
        for p in Project.objects.filter(client=self.c):
            self.assertEqual(p.deadline, before[p.pk][0] + timedelta(days=3))
            self.assertEqual(p.agency_deadline, before[p.pk][1] + timedelta(days=3))
        # editor's deadline may not jump past the agency's
        d = self.act({"op": "shift", "days": "30", "which": "editor"})
        self.assertIn("Not moved", " ".join(m["text"] for m in d["messages"]))
        self.assertFalse(any(p.deadline > p.agency_deadline for p in Project.objects.filter(client=self.c)))
        self.assertFalse(self.act({"op": "shift", "days": "0"})["ok"])

    def test_add_film_uses_agency_rate(self):
        rate = self.ag.rates.filter(deliverable="Highlight").values_list("amount", flat=True).first() or 0
        d = self.act({"op": "add", "start_date": str(timezone.localdate()), "deliverable": "Highlight", "deadline": "", "agency_deadline": ""})
        self.assertTrue(d["ok"])
        p = Project.objects.get(client=self.c, deliverable="Highlight")
        self.assertEqual((p.couple, p.agency_id, p.invoice_amount), ("Meera & Dev", self.ag.pk, rate))
        bad = self.act({"op": "add", "start_date": str(timezone.localdate()), "deliverable": "Teaser", "deadline": "2030-02-10", "agency_deadline": "2030-02-01"})
        self.assertFalse(bad["ok"])

    def test_rename_updates_films_and_merges_on_clash(self):
        d = self.act({"op": "edit", "name": "Meera & Devansh", "wedding_date": "2026-12-12", "notes": "Udaipur"})
        self.assertTrue(d["ok"])
        self.c.refresh_from_db()
        self.assertEqual((self.c.name, str(self.c.wedding_date), self.c.notes), ("Meera & Devansh", "2026-12-12", "Udaipur"))
        self.assertEqual(set(Project.objects.filter(client=self.c).values_list("couple", flat=True)), {"Meera & Devansh"})
        other = Couple.for_name(self.ag, "Sara & Om")
        Project.objects.create(couple="Sara & Om", deliverable="Teaser", agency=self.ag)
        d = self.act({"op": "edit", "name": "sara and om", "wedding_date": "", "notes": ""})
        self.assertTrue(d["refresh"])
        self.assertFalse(Couple.objects.filter(pk=self.c.pk).exists())
        self.assertEqual(Project.objects.filter(client=other).count(), 5)

    def test_merge_look_alikes(self):
        Project.objects.create(couple="Meera & Deev", deliverable="Highlight", agency=self.ag)
        dup = Couple.objects.get(agency=self.ag, key=couple_key("Meera & Deev"))
        html = self.f.get(f"/projects/?agency={self.ag.pk}").content.decode()
        self.assertIn(f'data-dup="{dup.pk}-{self.c.pk}"', html)       # suggested: fold the 1-film couple into the 4-film one
        r = self.f.post(f"/projects/couple/{dup.pk}/action/", {"op": "merge", "into": self.c.pk, "qs": ""}, **FETCH)
        self.assertTrue(r.json()["refresh"])
        self.assertFalse(Couple.objects.filter(pk=dup.pk).exists())
        self.assertEqual(Project.objects.filter(client=self.c).count(), 5)
        self.assertEqual(Project.objects.filter(couple="Meera & Deev").count(), 0)

    def test_merge_never_crosses_agencies(self):
        other = Couple.for_name(Agency.objects.get(code="HFM"), "Meera & Dev")
        d = self.act({"op": "merge", "into": other.pk})
        self.assertFalse(d["ok"])
        self.assertTrue(Couple.objects.filter(pk=self.c.pk).exists())

    def test_plain_post_without_js_redirects_back(self):
        r = self.f.post(f"/projects/couple/{self.c.pk}/action/", {"op": "status", "status": "qc", "qs": "status=&q=Meera"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/projects/?status=&q=Meera")
        self.assertEqual(Project.objects.filter(client=self.c, status="qc").count(), 4)


class TeamLoadFromPlanner(SeededTestCase):
    """Dashboard 'Team load this week': every working day with a film/revisions planned = Full."""

    def test_full_vs_can_take_more(self):
        from django.contrib.auth import get_user_model
        from ops.models import DayLog, PlanEntry, StudioSettings
        from ops.services import week_start
        from ops.views.admin import week_load
        U = get_user_model()
        today = timezone.localdate()
        a = week_start(today)
        days = [a + timedelta(days=i) for i in range(7)]
        work = [d for d in days if d.weekday() in StudioSettings.get().weekdays]
        pratik, harsh = U.objects.get(username="pratik"), U.objects.get(username="harsh")
        PlanEntry.objects.filter(user__in=[pratik, harsh], date__range=(a, days[-1])).delete()
        DayLog.objects.filter(user__in=[pratik, harsh], date__range=(a, days[-1])).delete()
        film = Project.objects.filter(status=Project.IN_PROGRESS).first()
        for d in work:
            PlanEntry.objects.create(user=pratik, date=d, slot=1, project=film)
        for d in work[:3]:
            PlanEntry.objects.create(user=harsh, date=d, slot=1, project=film)
        PlanEntry.objects.create(user=harsh, date=work[3], slot=1, label="Back-up")
        rows = {r["u"].username: r for r in week_load([pratik, harsh], today)[0]}
        self.assertEqual((rows["pratik"]["status"], rows["pratik"]["free"]), ("Full", 0))
        self.assertEqual(rows["harsh"]["status"], "Can take more")
        self.assertEqual((rows["harsh"]["assigned"], rows["harsh"]["free"]), (3, len(work) - 3))
        # a day on leave isn't a free day
        PlanEntry.objects.filter(user=pratik, date=work[0]).delete()
        DayLog.objects.create(user=pratik, date=work[0], day_type=DayLog.LEAVE)
        r = week_load([pratik], today)[0][0]
        self.assertEqual((r["status"], r["work"]), ("Full", len(work) - 1))
        html = self.login("ag").get("/dashboard/").content.decode()
        self.assertIn("Team load this week", html)
        self.assertIn("load-days", html)


class DeliveryTiming(SeededTestCase):
    """On time = sent to the agency on or before the agency's deadline; client revisions are never late."""

    def setUp(self):
        self.f = self.login("ag")
        self.today = timezone.localdate()
        self.p = Project.objects.filter(status=Project.IN_PROGRESS, editor__isnull=False).first()
        self.p.deadline = self.today - timedelta(days=5)          # editor was late…
        self.p.agency_deadline = self.today - timedelta(days=2)   # …but the agency still got it in time
        self.p.save()

    def post(self, data):
        return self.f.post(f"/projects/{self.p.pk}/status/", {**data, "next": "/projects/"}, **FETCH).json()

    def test_sent_date_picked_and_on_time_against_agency_deadline(self):
        sent = self.today - timedelta(days=3)
        d = self.post({"status": Project.SENT_AGENCY, "sent_on": sent.isoformat()})
        self.assertTrue(d["ok"])
        self.assertIn("On time", d["messages"][0]["text"])
        self.p.refresh_from_db()
        self.assertEqual((self.p.status, self.p.first_delivered_on), (Project.SENT_AGENCY, sent))
        self.assertFalse(self.p.delivered_late)
        self.assertEqual(self.p.timing_label, "Delivered on time")
        self.assertIn("sent " + format(sent.day), d["row"])
        # correcting the date later (still Sent to agency) moves it
        d = self.post({"status": Project.SENT_AGENCY, "sent_on": self.today.isoformat()})
        self.p.refresh_from_db()
        self.assertEqual(self.p.first_delivered_on, self.today)
        self.assertTrue(self.p.delivered_late)
        # undo puts the dates back
        undo = d["undo"]
        self.f.post(f"/projects/{self.p.pk}/status/", {**undo, "next": "/projects/"}, **FETCH)
        self.p.refresh_from_db()
        self.assertEqual(self.p.first_delivered_on, sent)

    def test_future_sent_date_refused(self):
        d = self.post({"status": Project.SENT_AGENCY, "sent_on": (self.today + timedelta(days=1)).isoformat()})
        self.assertFalse(d["ok"])
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, Project.IN_PROGRESS)

    def test_no_sent_date_means_today(self):
        self.post({"status": Project.SENT_AGENCY})
        self.p.refresh_from_db()
        self.assertEqual(self.p.first_delivered_on, self.today)
        self.assertTrue(self.p.delivered_late)

    def test_client_revision_is_never_late_or_overdue(self):
        Project.objects.filter(pk=self.p.pk).update(status=Project.CLIENT_REV, first_delivered_on=self.today,
                                                     deadline=self.today - timedelta(days=3))
        self.p.refresh_from_db()
        self.assertIsNone(self.p.days_left)
        self.assertNotEqual(self.p.flag, "Overdue")
        self.assertFalse(self.p.delivered_late)
        late = self.f.get("/projects/?view=list&status=late").context["jobs"]
        self.assertNotIn(self.p.pk, [j["p"].pk for j in late])
        overdue = self.f.get("/projects/?view=list&status=&due=overdue").context["jobs"]
        self.assertNotIn(self.p.pk, [j["p"].pk for j in overdue])
        self.assertNotIn(self.p, self.f.get("/dashboard/").context["overdue"])

    def test_dashboard_has_no_missing_logs(self):
        self.assertNotContains(self.f.get("/dashboard/"), "Missing logs yesterday")

    def test_edit_form_sent_date(self):
        from ops.forms import ProjectForm
        f = ProjectForm(instance=self.p)
        self.assertIn("first_delivered_on", f.fields)
        self.assertEqual(f.fields["first_delivered_on"].label, "Sent to agency on")


class PlannerMatchesProjects(SeededTestCase):
    """Planner and Projects agree on who is editing a film."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from ops.models import PlanEntry
        from ops.services import is_workday_for
        U = get_user_model()
        self.PE = PlanEntry
        self.f = self.login("ag")
        self.pratik, self.nikhil = U.objects.get(username="pratik"), U.objects.get(username="nikhil")
        self.today = timezone.localdate()
        d = self.today + timedelta(days=3)
        while not (is_workday_for(self.pratik, d) and is_workday_for(self.nikhil, d)):
            d += timedelta(days=1)
        self.d = d
        self.PE.objects.filter(user__in=[self.pratik, self.nikhil], date__gte=self.today).delete()
        self.p = Project.objects.create(couple="Plan Check", deliverable="Teaser", agency=Agency.objects.get(code="BRC"),
                                        editor=self.pratik, deadline=self.today + timedelta(days=20))

    def plan(self, user, value, **extra):
        return self.f.post("/planner/", {"user": user.pk, "date": self.d.isoformat(), "value": value, **extra},
                           **FETCH).json()

    def test_planning_unassigned_film_makes_that_person_the_editor(self):
        Project.objects.filter(pk=self.p.pk).update(editor=None)
        r = self.plan(self.nikhil, f"p:{self.p.pk}")
        self.assertIn("Nikhil is now its editor", r["message"])
        self.p.refresh_from_db()
        self.assertEqual(self.p.editor, self.nikhil)
        # Undo puts the cell back and the film back to unassigned
        self.f.post("/planner/", r["undo"], **FETCH)
        self.p.refresh_from_db()
        self.assertIsNone(self.p.editor_id)

    def test_mismatch_is_flagged_and_fixed(self):
        r = self.plan(self.nikhil, f"p:{self.p.pk}")
        self.assertIn("editor on Projects is Pratik", r["message"])
        self.assertIn("Make Nikhil the editor", r["mismatch"])
        self.assertIn("mismatch", next(iter(r["cells"].values()))["html"])
        html = self.f.get("/projects/?view=list&status=").content.decode()
        self.assertIn("⚠ Planner: Nikhil", html)
        # Move the day to the editor on Projects
        r = self.f.post("/planner/", {"action": "fix", "op": "move", "project": self.p.pk, "user": self.nikhil.pk}, **FETCH).json()
        self.assertTrue(r["ok"])
        self.assertFalse(self.PE.objects.filter(project=self.p, user=self.nikhil).exists())
        self.assertTrue(self.PE.objects.filter(project=self.p, user=self.pratik, date=self.d).exists())
        self.assertNotIn("Make Nikhil", r["mismatch"])
        # …or make the planned person the editor
        self.plan(self.nikhil, f"p:{self.p.pk}")
        r = self.f.post("/planner/", {"action": "fix", "op": "make", "project": self.p.pk, "user": self.nikhil.pk}, **FETCH).json()
        self.p.refresh_from_db()
        self.assertEqual(self.p.editor, self.nikhil)
        self.assertFalse(self.PE.objects.filter(project=self.p, user=self.pratik, date__gte=self.today).exists())

    def test_revisions_by_someone_else_are_fine(self):
        self.plan(self.nikhil, f"r:{self.p.pk}")
        from ops.services import plan_mismatches
        self.assertFalse(any(m["project"].pk == self.p.pk for m in plan_mismatches()))

    def test_done_on_hold_or_unassigned_clears_days_ahead(self):
        past = self.today - timedelta(days=1)
        self.PE.objects.create(user=self.pratik, date=past, project=self.p)
        self.PE.objects.create(user=self.pratik, date=self.d, project=self.p)
        self.PE.objects.create(user=self.nikhil, date=self.d, project=self.p, label="Revisions")
        self.p.status = Project.SENT_AGENCY
        self.p.save()
        self.assertFalse(self.PE.objects.filter(project=self.p, date=self.d, label="").exists())
        self.assertTrue(self.PE.objects.filter(project=self.p, date=past).exists())          # history stays
        self.assertTrue(self.PE.objects.filter(project=self.p, label="Revisions").exists())  # revisions stay
        self.p.status = Project.IN_PROGRESS
        self.p.save()
        self.PE.objects.create(user=self.pratik, date=self.d, project=self.p)
        self.f.post(f"/projects/{self.p.pk}/status/", {"editor": "", "next": "/projects/"}, **FETCH)
        self.assertFalse(self.PE.objects.filter(project=self.p, date=self.d, label="").exists())

    def test_extra_work_follows_its_editor(self):
        Project.objects.filter(pk=self.p.pk).update(is_extra=True)
        self.p.refresh_from_db()
        self.PE.objects.create(user=self.pratik, date=self.d, project=self.p)
        self.f.post(f"/projects/{self.p.pk}/status/", {"editor": self.nikhil.pk, "next": "/projects/"}, **FETCH)
        self.assertFalse(self.PE.objects.filter(project=self.p, user=self.pratik, date=self.d).exists())

    def test_planner_shows_strip(self):
        self.plan(self.nikhil, f"p:{self.p.pk}")
        self.assertContains(self.f.get("/planner/"), "planned for someone who isn")
        self.assertContains(self.f.get("/planner/?view=month"), "data-plan-mismatch")


class DriveNotInUse(SeededTestCase):
    def test_not_in_use_status(self):
        from ops.models import HardDrive
        d = HardDrive.objects.create(code="HDD-99", status="idle")
        self.assertEqual(d.get_status_display(), "Not in use")
        f = self.login("ag")
        r = f.get("/drives/")
        self.assertContains(r, "Not in use")
        self.assertIn(("idle", "Not in use", 1), r.context["chips"])
        r = f.get("/drives/?status=idle")
        self.assertEqual([x.code for x in r.context["drives"]], ["HDD-99"])
        self.assertContains(f.get(f"/drives/{d.pk}/"), 'value="idle" selected')


class DriveFolders(SeededTestCase):
    """Folders pasted for a drive → founders add them as projects or decline; all searchable."""

    def setUp(self):
        from ops.models import DriveFolder, HardDrive
        self.DF = DriveFolder
        self.ag = Agency.objects.get(code="BRC")
        self.d = HardDrive.objects.create(code="HDD-77", agency=self.ag)
        self.f = self.login("ag")

    def add(self, text, who=None):
        c = who or self.f
        return c.post(f"/drives/{self.d.pk}/folders/", {"folders": text}, **FETCH).json()

    def test_parse_folder_names(self):
        from ops.folders import parse
        self.assertEqual(parse(r"E:\2026\Amit_Tanya_Wedding")["couple"], "Amit & Tanya")
        r = parse("Riya & Arjun - Teaser+Trailer")
        self.assertEqual((r["couple"], r["films"]), ("Riya & Arjun", ["Teaser", "Trailer"]))
        r = parse("20261102_Meera_Dev_Sangeet")
        self.assertEqual((r["couple"], str(r["date"])), ("Meera & Dev", "2026-11-02"))
        self.assertEqual(parse("/Volumes/HDD/Sara weds Om Full Film")["films"], ["Full Film"])
        self.assertIsNone(parse("   "))

    def test_paste_list_adds_folders_once(self):
        r = self.add("E:\\2026\\Amit_Tanya_Wedding\nRiya & Arjun - Teaser+Trailer\n\nE:\\2026\\Amit_Tanya_Wedding\n")
        self.assertTrue(r["ok"])
        self.assertEqual(self.DF.objects.filter(drive=self.d).count(), 2)
        self.assertIn("data-drive-folders", r["swap"][0]["html"])
        self.add("Riya & Arjun - Teaser+Trailer")
        self.assertEqual(self.DF.objects.filter(drive=self.d).count(), 2)

    def test_drive_desk_can_add_but_not_decide(self):
        from django.contrib.auth import get_user_model
        from ops.rbac import DRIVE
        desk = [u for u in get_user_model().objects.all() if getattr(u.profile, "role", "") == DRIVE]
        if not desk:
            self.skipTest("no drive desk user in seed")
        c = self.client_class()
        c.force_login(desk[0])
        self.add("Neha_Karan", who=c)
        f = self.DF.objects.get(drive=self.d)
        r = c.post(f"/projects/folders/{f.pk}/", {"op": "accept", "films": ["Teaser"]})
        self.assertNotEqual(r.status_code, 200)
        f.refresh_from_db()
        self.assertEqual(f.status, "pending")

    def test_accept_creates_films_on_drive_under_couple(self):
        self.add("Rhea & Aryan - Teaser+Trailer")
        f = self.DF.objects.get(drive=self.d)
        self.assertContains(self.f.get("/projects/"), "New folders from drives")
        r = self.f.post(f"/projects/folders/{f.pk}/", {"op": "accept", "start_date": str(timezone.localdate()), "couple": "Rhea & Aryan",
                                                       "films": ["Teaser", "Trailer", "Reels"]}, **FETCH).json()
        self.assertTrue(r["ok"])
        self.assertTrue(r["refresh"])
        films = Project.objects.filter(couple="Rhea & Aryan", agency=self.ag)
        self.assertEqual(sorted(films.values_list("deliverable", flat=True)), ["Reels", "Teaser", "Trailer"])
        self.assertTrue(all(p.drive_id == self.d.pk for p in films))
        f.refresh_from_db()
        self.assertEqual((f.status, f.client.name), ("accepted", "Rhea & Aryan"))
        self.assertNotContains(self.f.get("/projects/"), "New folders from drives")
        # a second folder of the same couple doesn't duplicate films
        self.add("Rhea_Aryan_Reception")
        g = self.DF.objects.get(drive=self.d, status="pending")
        r = self.f.post(f"/projects/folders/{g.pk}/", {"op": "accept", "start_date": str(timezone.localdate()), "couple": "rhea and aryan",
                                                       "films": ["Teaser", "Full Film"]}, **FETCH).json()
        self.assertEqual(Project.objects.filter(couple="Rhea & Aryan", agency=self.ag).count(), 4)
        self.assertIn("already had Teaser", " ".join(m["text"] for m in r["messages"]))

    def test_decline_keeps_folder_on_drive_and_can_review_again(self):
        self.add("Old_Backup_Stuff")
        f = self.DF.objects.get(drive=self.d)
        self.f.post(f"/projects/folders/{f.pk}/", {"op": "decline"}, **FETCH)
        f.refresh_from_db()
        self.assertEqual(f.status, "declined")
        self.assertFalse(Project.objects.filter(drive=self.d).exists())
        self.assertContains(self.f.get(f"/drives/{self.d.pk}/"), "Old_Backup_Stuff")
        self.f.post(f"/drives/folders/{f.pk}/", {"op": "review"}, **FETCH)
        f.refresh_from_db()
        self.assertEqual(f.status, "pending")

    def test_bulk_accept_and_decline(self):
        self.add("Amit_Tanya_Teaser\nSara weds Om\nJunk_Folder")
        a, s, j = (self.DF.objects.get(drive=self.d, path=p) for p in ("Amit_Tanya_Teaser", "Sara weds Om", "Junk_Folder"))
        r = self.f.post("/projects/folders/", {"op": "accept", "start_date": str(timezone.localdate()), "ids": [a.pk, s.pk]}, **FETCH).json()
        self.assertTrue(Project.objects.filter(couple="Amit & Tanya", deliverable="Teaser").exists())
        self.assertIn("Sara weds Om", " ".join(m["text"] for m in r["messages"]))   # no films read: left for one-by-one
        self.f.post("/projects/folders/", {"op": "accept", "start_date": str(timezone.localdate()), "ids": [s.pk], "films": ["Full Film"]}, **FETCH)
        self.assertTrue(Project.objects.filter(couple="Sara & Om", deliverable="Full Film").exists())
        self.f.post("/projects/folders/", {"op": "decline", "ids": [j.pk]}, **FETCH)
        j.refresh_from_db()
        self.assertEqual(j.status, "declined")

    def test_search_finds_folders_and_couple_shows_drive(self):
        self.add("E:\\2026\\Amit_Tanya_Wedding\nJunk")
        r = self.f.get("/drives/?q=tanya")
        self.assertContains(r, "Folders matching")
        self.assertContains(r, "2026/Amit_Tanya_Wedding")
        self.assertIn(self.d, list(r.context["drives"]))
        f = self.DF.objects.get(path="2026/Amit_Tanya_Wedding")
        self.f.post(f"/projects/folders/{f.pk}/", {"op": "accept", "start_date": str(timezone.localdate()), "couple": "Amit & Tanya", "films": ["Teaser"]}, **FETCH)
        self.assertContains(self.f.get("/projects/?q=Amit"), "HDD-77")


class DriveRegisterFilters(SeededTestCase):
    def test_filters_and_search(self):
        from ops.models import DriveFolder, HardDrive
        ag = Agency.objects.get(code="BRC")
        a = HardDrive.objects.create(code="SSD-50", kind="SSD", agency=ag)
        b = HardDrive.objects.create(code="HDD-51", kind="HDD", studio_owned=True, status="sent")
        DriveFolder.objects.create(drive=a, path="2026/Zara_Kabir_Wedding", name="Zara_Kabir_Wedding", couple_guess="Zara & Kabir")
        f = self.login("ag")
        codes = lambda url: [d.code for d in f.get(url).context["drives"]]
        self.assertIn("SSD-50", codes("/drives/?kind=SSD"))
        self.assertNotIn("HDD-51", codes("/drives/"))                     # sent back = not in studio
        self.assertIn("HDD-51", codes("/drives/?status=all&agency=studio"))
        self.assertEqual(codes("/drives/?folders=pending"), ["SSD-50"])
        r = f.get("/drives/?q=zara kabir")
        self.assertEqual([h.drive.code for h in r.context["hits"]], ["SSD-50"])
        self.assertEqual(codes("/drives/?q=zara"), ["SSD-50"])
        self.assertContains(f.get("/drives/?q=Zara"), "<mark>Zara</mark>")
        # fluid: the page has a live filter form and parts
        self.assertContains(r, "data-live-filter")
        self.assertContains(r, 'data-live-part="grid"')


class DriveReceiveAndDelete(SeededTestCase):
    def test_receive_with_folders_then_delete(self):
        from ops.models import DriveFolder, HardDrive
        f = self.login("ag")
        r = f.get("/drives/")
        self.assertContains(r, "+ Receive a drive")
        self.assertContains(r, 'name="folder_list"')
        ag = Agency.objects.get(code="BRC")
        r = f.post("/drives/", {"code": "HDD-88", "kind": "HDD", "agency": ag.pk, "received_on": "2026-11-10",
                                "received_via": "courier", "status": "in_use",
                                "folder_list": "E:\\2026\\Amit_Tanya_Wedding\nKiran_Dev_Teaser"})
        d = HardDrive.objects.get(code="HDD-88")
        self.assertRedirects(r, f"/drives/{d.pk}/", fetch_redirect_response=False)
        self.assertEqual(DriveFolder.objects.filter(drive=d).count(), 2)
        p = Project.objects.create(couple="X & Y", deliverable="Teaser", agency=ag, drive=d)
        # editors and the drive desk can't delete
        self.assertNotEqual(self.login("pratik").post(f"/drives/{d.pk}/delete/").status_code, 200)
        self.assertTrue(HardDrive.objects.filter(pk=d.pk).exists())
        r = f.post(f"/drives/{d.pk}/delete/", **FETCH).json()
        self.assertEqual(r["remove"], [f'[data-drive-cell="{d.pk}"]'])
        self.assertFalse(HardDrive.objects.filter(pk=d.pk).exists())
        self.assertFalse(DriveFolder.objects.filter(path="2026/Amit_Tanya_Wedding", drive_id=d.pk).exists())
        p.refresh_from_db()
        self.assertIsNone(p.drive_id)


class DriveDataListPhotos(SeededTestCase):
    def setUp(self):
        from ops.models import HardDrive, StoredImage
        self.ag = Agency.objects.get(code="BRC")
        self.a = HardDrive.objects.create(code="HDD-91", agency=self.ag, capacity_tb=4, data_gb=3500)
        self.b = HardDrive.objects.create(code="HDD-92", agency=self.ag, capacity_tb=2, data_gb=200)
        self.img = StoredImage.objects.create(data=b"\xff\xd8\xff", content_type="image/jpeg")
        self.a.photos.add(self.img)
        self.f = self.login("ag")

    def test_label_and_sort_by_data(self):
        from ops.forms import DriveForm
        self.assertEqual(DriveForm().fields["data_gb"].label, "Data Available (GB)")
        codes = [d.code for d in self.f.get("/drives/?sort=data_desc").context["drives"]]
        self.assertLess(codes.index("HDD-91"), codes.index("HDD-92"))
        codes = [d.code for d in self.f.get("/drives/?sort=data_asc").context["drives"]]
        self.assertLess(codes.index("HDD-92"), codes.index("HDD-91"))
        self.assertContains(self.f.get("/drives/"), "3500 GB data available")

    def test_list_view_with_hover_photo(self):
        r = self.f.get("/drives/?view=list")
        self.assertContains(r, "drive-table")
        self.assertContains(r, 'class="ph-pop"')
        self.assertContains(r, f"/img/{self.img.pk}/")
        self.assertContains(self.f.get("/drives/"), "drive-grid")

    def test_delete_photo(self):
        from ops.models import StoredImage
        self.assertContains(self.f.get(f"/drives/{self.a.pk}/"), f'data-photo="{self.img.pk}"')
        self.assertNotEqual(self.login("pratik").post(f"/photos/{self.img.pk}/delete/").status_code, 200)
        r = self.f.post(f"/photos/{self.img.pk}/delete/", **FETCH).json()
        self.assertEqual(r["remove"], [f'[data-photo="{self.img.pk}"]'])
        self.assertFalse(StoredImage.objects.filter(pk=self.img.pk).exists())
        self.assertEqual(self.a.photos.count(), 0)

    def test_migration_converts_tb_to_gb(self):
        from decimal import Decimal
        from importlib import import_module
        mig = import_module("ops.migrations.0016_drive_data_gb")

        class D:   # stand-in row
            data_tb = Decimal("1.8")
            def save(self, update_fields):
                self.saved = update_fields
        d = D()

        class QS(list):
            def exclude(self, **kw):
                return self

        class FakeModel:
            objects = QS([d])

        class Apps:
            def get_model(self, *a):
                return FakeModel
        mig.tb_to_gb(Apps(), None)
        self.assertEqual(d.data_gb, Decimal("1800.0"))


class DriveDataFix(SeededTestCase):
    def test_wrongly_multiplied_values_are_fixed(self):
        from decimal import Decimal
        from importlib import import_module
        from django.apps import apps as django_apps
        from ops.models import HardDrive
        a = HardDrive.objects.create(code="HDD-71", capacity_tb=4, data_gb=106000)    # was 106 GB
        b = HardDrive.objects.create(code="HDD-72", capacity_tb=4, data_gb=1800)      # was 1.8 TB: right
        c = HardDrive.objects.create(code="HDD-73", data_gb=250000)                   # size unknown, 250 GB
        import_module("ops.migrations.0017_fix_drive_data_gb").fix(django_apps, None)
        for d, want in ((a, "106.0"), (b, "1800"), (c, "250.0")):
            d.refresh_from_db()
            self.assertEqual(d.data_gb, Decimal(want))

    def test_form_refuses_more_than_capacity(self):
        from ops.forms import DriveForm
        f = DriveForm(data={"code": "HDD-74", "kind": "HDD", "studio_owned": "on", "capacity_tb": "2", "data_gb": "106000",
                            "received_on": "2026-11-11", "received_via": "courier", "status": "in_use"})
        self.assertFalse(f.is_valid())
        self.assertIn("data_gb", f.errors)
        f = DriveForm(data={"code": "HDD-74", "kind": "HDD", "studio_owned": "on", "capacity_tb": "2", "data_gb": "106",
                            "received_on": "2026-11-11", "received_via": "courier", "status": "in_use"})
        self.assertTrue(f.is_valid(), f.errors)


class DriveListSentBack(SeededTestCase):
    def test_sent_back_column_and_row_link(self):
        from ops.models import DriveReturn, HardDrive
        d = HardDrive.objects.create(code="HDD-81", agency=Agency.objects.get(code="BRC"), status="sent")
        DriveReturn.objects.create(drive=d, sent_to="Rahul (BRC)", method="courier")
        r = self.login("ag").get("/drives/?view=list&status=all")
        self.assertContains(r, "<th>Sent back</th>", html=False)
        self.assertContains(r, "to Rahul (BRC)")
        self.assertContains(r, f'data-href="/drives/{d.pk}/"')


class DriveSentBackDate(SeededTestCase):
    """The drive desk / founders can add (or correct) the date a drive was sent back, on the
    drive's page and straight from the register list."""

    def setUp(self):
        from datetime import timedelta
        from ops.models import HardDrive
        self.today = timezone.localdate()
        self.d = HardDrive.objects.create(code="HDD-91", agency=Agency.objects.get(code="BRC"), status="ready",
                                          received_on=self.today - timedelta(days=20))
        self.f = self.login("ag")

    def edit(self, **over):
        data = {"code": "HDD-91", "kind": "HDD", "agency": self.d.agency_id, "received_on": self.d.received_on.isoformat(),
                "received_via": "courier", "status": "ready", **over}
        return self.f.post(f"/drives/{self.d.pk}/", data, follow=True)

    def test_date_on_the_drive_page(self):
        from datetime import timedelta
        from ops.models import DriveReturn
        self.assertContains(self.f.get(f"/drives/{self.d.pk}/"), "Sent back on")
        on = self.today - timedelta(days=3)
        r = self.edit(sent_back_on=on.isoformat())
        self.assertContains(r, f"Sent back on {on:%-d %b %Y}")
        self.d.refresh_from_db()
        self.assertEqual(self.d.status, "sent")                       # the date sets the status
        ret = DriveReturn.objects.get(drive=self.d)
        self.assertEqual((ret.sent_on, ret.sent_to), (on, "BadalRaja Company" if not self.d.agency.contact_person
                                                       else f"{self.d.agency.name} – {self.d.agency.contact_person}"))
        # correcting the date changes that record, never adds a second one
        self.edit(status="sent", sent_back_on=(on - timedelta(days=1)).isoformat())
        self.assertEqual(DriveReturn.objects.filter(drive=self.d).count(), 1)
        self.assertEqual(DriveReturn.objects.get(drive=self.d).sent_on, on - timedelta(days=1))
        self.assertContains(self.f.get(f"/drives/{self.d.pk}/"), f'value="{on - timedelta(days=1):%Y-%m-%d}"')

    def test_page_validation(self):
        from datetime import timedelta
        r = self.edit(status="sent")
        self.assertContains(r, "Add the date it was sent back.")
        r = self.edit(sent_back_on=(self.today + timedelta(days=1)).isoformat())
        self.assertContains(r, "in the future")
        r = self.edit(sent_back_on=(self.d.received_on - timedelta(days=1)).isoformat())
        self.assertContains(r, "before it was received")
        self.d.refresh_from_db()
        self.assertEqual(self.d.status, "ready")
        # a drive that just came in has no sent-back date on its form
        self.assertNotContains(self.f.get("/drives/"), 'name="sent_back_on"')

    def test_inline_from_the_register(self):
        from datetime import timedelta
        from ops.models import DriveReturn
        r = self.f.get("/drives/?view=list&status=all")
        self.assertContains(r, f'data-sent-cell="{self.d.pk}"')
        self.assertContains(r, "+ Sent back date")
        on = self.today - timedelta(days=2)
        res = self.f.post(f"/drives/{self.d.pk}/sent-on/", {"sent_on": on.isoformat()}, **FETCH).json()
        self.assertTrue(res["ok"])
        self.assertEqual(res["swap"][0]["sel"], f'[data-sent-cell="{self.d.pk}"]')
        self.assertIn(f"{on:%-d %b %Y}", res["swap"][0]["html"])
        self.d.refresh_from_db()
        self.assertEqual(self.d.status, "sent")
        self.assertEqual(DriveReturn.objects.get(drive=self.d).sent_on, on)
        bad = self.f.post(f"/drives/{self.d.pk}/sent-on/", {"sent_on": (self.today + timedelta(days=2)).isoformat()}, **FETCH).json()
        self.assertFalse(bad["ok"])
        self.assertEqual(DriveReturn.objects.get(drive=self.d).sent_on, on)
        # the drive desk can do it; editors can't
        self.assertTrue(self.login("drives").post(f"/drives/{self.d.pk}/sent-on/", {"sent_on": on.isoformat()}, **FETCH).json()["ok"])
        self.assertNotEqual(self.login("pratik").post(f"/drives/{self.d.pk}/sent-on/", {"sent_on": on.isoformat()}).status_code, 200)

    def test_sent_without_a_date_is_flagged(self):
        self.d.status = "sent"
        self.d.save()
        r = self.f.get("/drives/?view=list&status=sent")
        self.assertContains(r, "Date missing")
        self.assertContains(self.f.get("/drives/?status=sent"), "sent-back date missing")


class StartDates(SeededTestCase):
    """Projects get a start date; the Planner plans from it and shows start + due days."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from ops.models import PlanEntry
        self.PE = PlanEntry
        self.f = self.login("ag")
        self.today = timezone.localdate()
        self.ed = get_user_model().objects.get(username="nikhil")
        self.PE.objects.filter(user=self.ed, date__gte=self.today).delete()
        self.p = Project.objects.create(couple="Start Check", deliverable="Teaser", agency=Agency.objects.get(code="BRC"),
                                        editor=self.ed, deadline=self.today + timedelta(days=30))

    def post(self, data):
        return self.f.post(f"/projects/{self.p.pk}/status/", {**data, "next": "/projects/"}, **FETCH).json()

    def test_start_date_inline_edit_moves_planner_days(self):
        from ops.services import auto_plan
        auto_plan(self.p)
        self.assertTrue(self.PE.objects.filter(project=self.p, date__lt=self.today + timedelta(days=10)).exists())
        start = self.today + timedelta(days=10)
        r = self.post({"start_date": start.isoformat()})
        self.assertTrue(r["ok"])
        self.assertIn('name="start_date"', r["row"])
        self.p.refresh_from_db()
        self.assertEqual(self.p.start_date, start)
        days = list(self.PE.objects.filter(project=self.p).values_list("date", flat=True))
        self.assertTrue(days)
        self.assertTrue(all(d >= start for d in days))
        self.assertEqual(r["undo"], {"start_date": ""})

    def test_start_after_deadline_refused(self):
        r = self.post({"start_date": (self.today + timedelta(days=40)).isoformat()})
        self.assertFalse(r["ok"])
        self.p.refresh_from_db()
        self.assertIsNone(self.p.start_date)

    def test_planner_shows_start_and_due(self):
        start = self.today + timedelta(days=2)
        Project.objects.filter(pk=self.p.pk).update(start_date=start, deadline=self.today + timedelta(days=9))
        html = self.f.get(f"/planner/?start={self.today.isoformat()}").content.decode()
        self.assertIn("▶ Start: Start Check · Teaser", html)
        self.assertIn("Due: Start Check · Teaser", html)
        self.assertIn("▶ Start day", self.f.get("/planner/?view=month").content.decode())

    def test_new_project_and_list(self):
        from ops.forms import NewProjectForm, ProjectForm
        self.assertIn("start_date", NewProjectForm().fields)
        self.assertIn("start_date", ProjectForm(instance=self.p).fields)
        self.assertContains(self.f.get("/projects/?view=list&status="), "<th data-col=\"start\">Start date</th>", html=False)
        codes = [j["p"].pk for j in self.f.get("/projects/?view=list&status=&sort=start").context["jobs"]]
        self.assertIn(self.p.pk, codes)


class MemorySafety(SeededTestCase):
    """The free host has 512 MB: photo handling must stay small."""

    def test_big_photo_upload_is_processed_at_low_memory(self):
        import io
        import resource
        import subprocess
        import sys
        code = (
            "import io,resource,django,os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');django.setup()\n"
            "from PIL import Image\n"
            "b=io.BytesIO();Image.new('RGB',(8000,5000),(120,80,60)).save(b,'JPEG',quality=90);d=b.getvalue()\n"
            "import gc;gc.collect();base=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
            "from ops import services as S\n"
            "from unittest import mock\n"
            "with mock.patch.object(S.StoredImage.objects,'create',lambda **k:k):\n"
            "    out=S.store_image(io.BytesIO(d))\n"
            "im=Image.open(io.BytesIO(out['data']));print(max(im.size),(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss-base)//1024)\n")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=".", env={**__import__("os").environ, "DEBUG": "1"})
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        side, extra_mb = map(int, r.stdout.split())
        self.assertEqual(side, 1600)
        self.assertLess(extra_mb, 120)       # was ~300 MB for a 40 MP photo

    def test_photo_bytes_not_loaded_in_lists(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from ops.models import HardDrive, StoredImage
        d = HardDrive.objects.create(code="HDD-61")
        d.photos.add(StoredImage.objects.create(data=b"x" * 1000))
        f = self.login("ag")
        with CaptureQueriesContext(connection) as q:
            f.get("/drives/?view=list")
        photo_sql = [x["sql"] for x in q.captured_queries if "ops_storedimage" in x["sql"]]
        self.assertTrue(photo_sql)
        self.assertFalse(any('"data"' in s for s in photo_sql), photo_sql)
        img = d.photos.first()
        self.assertEqual(bytes(f.get(f"/img/{img.pk}/").content), b"x" * 1000)    # still served in full


class WorkLog(SeededTestCase):
    """Founders see an editor's whole month: films, stages, hours, planned vs logged."""

    def setUp(self):
        from datetime import time
        from django.contrib.auth import get_user_model
        from ops.models import DayLog, PlanEntry, TaskEntry
        self.u = get_user_model().objects.get(username="pratik")
        self.f = self.login("ag")
        self.d = timezone.localdate().replace(day=2) if timezone.localdate().day > 2 else timezone.localdate()
        DayLog.objects.filter(user=self.u, date=self.d).delete()
        PlanEntry.objects.filter(user=self.u, date=self.d).delete()
        self.p = Project.objects.create(couple="Log Check", deliverable="Trailer", agency=Agency.objects.get(code="BRC"))
        self.q = Project.objects.create(couple="Plan Only", deliverable="Teaser", agency=Agency.objects.get(code="BRC"))
        log = DayLog.objects.create(user=self.u, date=self.d, day_type=DayLog.OFFICE, time_in=time(10), time_out=time(19),
                                    notes="first cut")
        TaskEntry.objects.create(daylog=log, project=self.p, stage="Edit", hours=5)
        PlanEntry.objects.create(user=self.u, date=self.d, slot=1, project=self.q)

    def test_month_view(self):
        r = self.f.get(f"/team/work-log/?u={self.u.pk}&m={self.d:%Y-%m}")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("Log Check", html)
        self.assertIn("first cut", html)
        self.assertIn("different from plan", html)
        day = next(x for x in r.context["days"] if x["d"] == self.d)
        self.assertEqual([t["h"] for t in day["tasks"]], [5.0])
        self.assertTrue(any(f["p"].pk == self.p.pk and f["h"] == 5.0 for f in r.context["films"]))
        self.assertEqual(len(r.context["days"]), (__import__("ops.services", fromlist=["x"]).month_end(self.d) - self.d.replace(day=1)).days + 1)

    def test_csv(self):
        r = self.f.get(f"/team/work-log/?u={self.u.pk}&m={self.d:%Y-%m}&format=csv")
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        body = r.content.decode()
        self.assertIn("Log Check", body)
        self.assertIn(self.d.isoformat(), body)

    def test_only_founders(self):
        self.assertNotEqual(self.login("pratik").get("/team/work-log/").status_code, 200)

    def test_linked_from_team(self):
        self.assertContains(self.f.get("/team/"), f"/team/work-log/?u={self.u.pk}")


class DatabaseBandwidth(SeededTestCase):
    """Supabase free plan: keep data pulled from the database small."""

    def test_photos_cached_by_browser_and_304_without_bytes(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from ops.models import HardDrive, StoredImage
        img = StoredImage.objects.create(data=b"y" * 5000)
        HardDrive.objects.create(code="HDD-55").photos.add(img)
        f = self.login("ag")
        r = f.get(f"/img/{img.pk}/")
        self.assertIn("immutable", r["Cache-Control"])
        etag = r["ETag"]
        with CaptureQueriesContext(connection) as q:
            r = f.get(f"/img/{img.pk}/", HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(r.status_code, 304)
        self.assertFalse(any('"data"' in x["sql"] for x in q.captured_queries))

    def test_prefetch_only_on_click(self):
        r = self.client.get("/speculation-rules.json")
        self.assertEqual(r.json()["prefetch"][0]["eagerness"], "conservative")

    def test_lists_skip_long_text(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        f = self.login("ag")
        with CaptureQueriesContext(connection) as q:
            f.get("/projects/?view=list&status=")
        film_sql = [x["sql"] for x in q.captured_queries if 'FROM "ops_project"' in x["sql"] and "ops_agency" in x["sql"]]
        self.assertTrue(film_sql)
        self.assertFalse(any('"brief"' in s or '"billing_address"' in s for s in film_sql))


class ExtraTaskPay(SeededTestCase):
    def setUp(self):
        from datetime import time
        from django.contrib.auth import get_user_model
        from ops.models import ExtraEntry
        self.u = get_user_model().objects.get(username="pratik")
        ag = Agency.objects.get(code="BRC")
        self.hourly = Project.objects.create(couple="Extra H", deliverable="Teaser", agency=ag, editor=self.u, is_extra=True,
                                             extra_basis="hourly", extra_rate=300)
        self.fixed = Project.objects.create(couple="Extra F", deliverable="Reels", agency=ag, editor=self.u, is_extra=True,
                                            extra_basis="fixed", extra_rate=0)
        today = timezone.localdate()
        self.e1 = ExtraEntry.objects.create(user=self.u, project=self.hourly, date=today, start=time(20), end=time(22))
        self.e2 = ExtraEntry.objects.create(user=self.u, project=self.fixed, date=today, start=time(21), end=time(23))
        self.f = self.login("ag")

    def test_set_amount_per_task_counts_in_payroll(self):
        from ops.services import extra_pay
        from ops.perf import clear
        month = timezone.localdate().replace(day=1)
        base = extra_pay(self.u, month)
        self.assertEqual(self.e1.pay, 600)       # 2 h x 300
        self.assertEqual(self.e2.pay, 0)         # fixed job, nothing per task yet
        r = self.f.post(f"/extra-work/{self.e2.pk}/amount/", {"amount": "1,500"}, HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertTrue(r["ok"])
        self.assertEqual({s["sel"] for s in r["swap"]}, {f'[data-extra="{self.e2.pk}"]', "[data-extra-summary]"})
        self.assertIn("1,500", r["swap"][1]["html"].replace("₹", "") if False else r["swap"][0]["html"] + r["messages"][0]["text"])
        self.e2.refresh_from_db()
        self.assertEqual(self.e2.pay, 1500)
        self.f.post(f"/extra-work/{self.e1.pk}/amount/", {"amount": "1000"}, HTTP_X_REQUESTED_WITH="fetch")
        clear()
        self.assertEqual(extra_pay(self.u, month), base + 1500 + 1000 - 600)
        # empty = back to the job's rate
        self.f.post(f"/extra-work/{self.e1.pk}/amount/", {"amount": ""}, HTTP_X_REQUESTED_WITH="fetch")
        self.e1.refresh_from_db()
        self.assertIsNone(self.e1.amount)
        self.assertEqual(self.e1.pay, 600)

    def test_validation_and_permissions(self):
        r = self.f.post(f"/extra-work/{self.e1.pk}/amount/", {"amount": "abc"}, HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertFalse(r["ok"])
        self.assertNotEqual(self.login("pratik").post(f"/extra-work/{self.e1.pk}/amount/", {"amount": "99999"}).status_code, 200)
        self.e1.refresh_from_db()
        self.assertIsNone(self.e1.amount)

    def test_page_lists_films_with_hours_on_hover(self):
        r = self.f.get("/extra-work/")
        self.assertContains(r, "Films this month")
        self.assertContains(r, f'data-extra-film="{self.hourly.pk}"')
        self.assertContains(r, f'data-extra-film="{self.fixed.pk}"')
        self.assertContains(r, 'class="hrs-tip"')
        self.assertContains(r, "20:00–22:00")           # the logged entry, shown on hover
        self.assertContains(r, 'placeholder="600"')     # hourly film: 2 h x 300 so far

    def test_pay_per_film(self):
        from ops.perf import clear
        from ops.services import extra_pay
        today = timezone.localdate()
        r = self.f.post(f"/extra-work/film/{self.fixed.pk}/pay/", {"amount": "5,000", "m": f"{today:%Y-%m}"},
                        HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertTrue(r["ok"])
        self.assertEqual({x["sel"] for x in r["swap"]}, {f'[data-extra-film="{self.fixed.pk}"]', "[data-extra-summary]"})
        self.fixed.refresh_from_db()
        self.assertEqual((self.fixed.extra_basis, self.fixed.extra_rate), ("fixed", 5000))
        month = today.replace(day=1)
        clear()
        before = extra_pay(self.u, month)
        self.fixed.status = Project.CLOSED      # paid with the payroll of the month it's delivered
        self.fixed.save()
        clear()
        self.assertEqual(extra_pay(self.u, month), before + 5000)
        # an hourly film switches to a set amount when one is typed
        self.f.post(f"/extra-work/film/{self.hourly.pk}/pay/", {"amount": "3000"}, HTTP_X_REQUESTED_WITH="fetch")
        self.hourly.refresh_from_db()
        self.assertEqual((self.hourly.extra_basis, self.hourly.extra_rate), ("fixed", 3000))
        self.assertNotEqual(self.login("pratik").post(f"/extra-work/film/{self.hourly.pk}/pay/", {"amount": "1"}).status_code, 200)

class ExtraNoAcceptNeeded(SeededTestCase):
    def test_editor_sees_film_and_amount_without_accepting(self):
        from datetime import time
        from django.contrib.auth import get_user_model
        from ops.models import ExtraEntry
        u = get_user_model().objects.get(username="pratik")
        ag = Agency.objects.get(code="BRC")
        offered = Project.objects.create(couple="No Accept", deliverable="Teaser", agency=ag, editor=u, is_extra=True,
                                         extra_basis="fixed", extra_rate=0)
        other = Project.objects.create(couple="Logged On", deliverable="Reels", agency=ag, editor=u)
        ExtraEntry.objects.create(user=u, project=other, date=timezone.localdate(), start=time(20), end=time(21))
        self.login("ag").post(f"/extra-work/film/{offered.pk}/pay/", {"amount": "1200"}, HTTP_X_REQUESTED_WITH="fetch")
        r = self.login("pratik").get("/my-extra/")
        self.assertNotContains(r, "New extra job offered to you")
        self.assertContains(r, "No Accept")
        self.assertContains(r, "₹1,200 for the film, paid when sent to the agency")
        self.assertContains(r, "Coming when sent to the agency")
        self.assertContains(r, "No Accept · Teaser: ₹1,200")
        self.assertContains(r, "Logged On")


class EstimatedProfit(SeededTestCase):
    def test_fy_profit_is_revenue_minus_full_year_costs(self):
        from datetime import date
        from ops.models import StudioSettings
        from ops.views.admin import fy_bounds, fy_estimate
        self.assertEqual(fy_bounds(date(2026, 11, 16)), (date(2026, 4, 1), date(2027, 3, 31)))
        self.assertEqual(fy_bounds(date(2027, 2, 1)), (date(2026, 4, 1), date(2027, 3, 31)))
        self.assertEqual(fy_bounds(date(2027, 4, 1)), (date(2027, 4, 1), date(2028, 3, 31)))
        s = StudioSettings.get()
        s.monthly_overheads = 25000
        s.save()
        s.tracking_start = date(2026, 11, 1)
        s.save()
        r = fy_estimate(date(2026, 11, 16))
        self.assertEqual((r["start"], r["end"], r["months"]), (date(2026, 11, 1), date(2027, 3, 31), 5))
        self.assertEqual(r["overheads"], 125000)                      # Nov-Mar = 5 months
        self.assertEqual(r["revenue"], r["delivered"] + r["open"])
        nxt = fy_estimate(date(2027, 6, 1))                           # next FY: full year from 1 April
        self.assertEqual((nxt["start"], nxt["months"]), (date(2027, 4, 1), 12))
        self.assertEqual(r["costs"], r["salaries"] + r["extra"] + r["contract"] + r["overheads"])
        self.assertEqual(r["profit"], r["revenue"] - r["costs"])

    def test_dashboard_tile_and_setting(self):
        f = self.login("ag")
        r = f.get("/dashboard/")
        self.assertContains(r, "Estimated profit · FY")
        self.assertContains(r, "Rent + electricity,")
        from ops.forms import StudioSettingsForm
        self.assertIn("monthly_overheads", StudioSettingsForm().fields)


class QueueAndFootage(SeededTestCase):
    def setUp(self):
        self.f = self.login("ag")
        self.ag = Agency.objects.get(code="BRC")

    def test_needs_an_editor_folded_by_agency_and_couple(self):
        a = Project.objects.create(couple="Queue Pair", deliverable="Teaser", agency=self.ag)
        b = Project.objects.create(couple="Queue Pair", deliverable="Trailer", agency=self.ag)
        r = self.f.get("/projects/")
        self.assertContains(r, 'class="card warn queue-box grp-queue"')
        self.assertContains(r, f'data-key="qa{self.ag.pk}"')
        self.assertContains(r, "Give all 2 to")
        # assign the whole couple at once
        from django.contrib.auth import get_user_model
        ed = get_user_model().objects.get(username="pratik")
        d = self.f.post(f"/projects/couple/{a.client_id}/action/", {"op": "assign", "editor": ed.pk, "refresh": "1",
                                                                    "ids": [a.pk, b.pk], "qs": ""}, **FETCH).json()
        self.assertTrue(d["refresh"])
        self.assertEqual(Project.objects.filter(pk__in=[a.pk, b.pk], editor=ed).count(), 2)

    def test_footage_is_editable(self):
        from ops.models import HardDrive
        d = HardDrive.objects.create(code="HDD-44", agency=self.ag)
        p = Project.objects.filter(agency=self.ag).first()
        html = self.f.get("/projects/?view=list&status=").content.decode()
        self.assertIn('name="footage"', html)
        r = self.f.post(f"/projects/{p.pk}/status/", {"footage": str(d.pk), "next": "/projects/"}, **FETCH).json()
        self.assertTrue(r["ok"])
        p.refresh_from_db()
        self.assertEqual(p.drive_id, d.pk)
        self.assertIn(f'value="{d.pk}" selected', r["row"])
        r = self.f.post(f"/projects/{p.pk}/status/", {"footage": "cloud", "next": "/projects/"}, **FETCH).json()
        p.refresh_from_db()
        self.assertEqual((p.drive_id, p.received_online), (None, True))
        self.f.post(f"/projects/{p.pk}/status/", r["undo"] | {"next": "/projects/"}, **FETCH)
        p.refresh_from_db()
        self.assertEqual(p.drive_id, d.pk)



class AgenciesFY(SeededTestCase):
    def test_agency_panel_uses_financial_year(self):
        f = self.login("ag")
        ag = Agency.objects.filter(currency="INR").first()
        before = {a["name"]: a["amount"] for a in f.get("/dashboard/").context["fy"]["agencies"]}
        Project.objects.create(couple="Panel Check", deliverable="Teaser", agency=ag, invoice_amount=12345)
        r = f.get("/dashboard/")
        after = {a["name"]: a["amount"] for a in r.context["fy"]["agencies"]}
        self.assertEqual(after[ag.name], before.get(ag.name, 0) + 12345)
        self.assertEqual(sum(after.values()), r.context["fy"]["revenue"])
        self.assertContains(r, "Agencies · FY")


class ValueVsCost(SeededTestCase):
    """Value of work: a film's invoice spread over its working days (start -> first sent);
    revisions add no value unless the film has a paid revision charge."""

    def setUp(self):
        from datetime import date
        from django.contrib.auth import get_user_model
        self.u = get_user_model().objects.get(username="soham")
        self.D = date
        self.p = Project.objects.create(couple="Value Check", deliverable="Trailer", agency=Agency.objects.get(code="BRC"),
                                        invoice_amount=18000, editor=self.u, start_date=date(2030, 11, 27),
                                        deadline=date(2030, 12, 5))

    def film(self, a, b):
        d = S.editor_value_detail(a, b).get(self.u.pk, {"value": 0, "films": []})
        return next((f for f in d["films"] if f["p"].pk == self.p.pk), None), d

    def test_spread_over_working_days(self):
        D = self.D
        nov_days = S.leave_working_days(self.u, D(2030, 11, 27), D(2030, 11, 30))
        all_days = S.leave_working_days(self.u, D(2030, 11, 27), D(2030, 12, 5))
        nov, _ = self.film(D(2030, 11, 1), D(2030, 11, 30))
        dec, _ = self.film(D(2030, 12, 1), D(2030, 12, 31))
        self.assertEqual(nov["days"], len(nov_days))
        self.assertEqual(nov["days"] + dec["days"], len(all_days))
        self.assertEqual(nov["v"], round(18000 * len(nov_days) / len(all_days)))
        self.assertAlmostEqual(nov["v"] + dec["v"], 18000, delta=1)
        # once sent later than planned, the same invoice spreads over more days
        self.p.first_delivered_on = D(2030, 12, 9)
        self.p.save()
        nov2, _ = self.film(D(2030, 11, 1), D(2030, 11, 30))
        self.assertLess(nov2["v"], nov["v"])

    def test_actual_days_worked_with_revisions(self):
        """Soham's trailer: planned 27 Nov - 5 Dec, worked those days, then 2 revision days."""
        from datetime import time, timedelta as td
        from ops.models import DayLog, TaskEntry, TaskStage
        D = self.D
        plan = S.leave_working_days(self.u, D(2030, 11, 27), D(2030, 12, 5))

        def work(day, stage=TaskStage.EDIT):
            log = DayLog.objects.create(user=self.u, date=day, day_type=DayLog.OFFICE, time_in=time(10), time_out=time(19))
            TaskEntry.objects.create(daylog=log, project=self.p, stage=stage, hours=8)

        nov = [d for d in plan if d.month == 11]
        for d in nov:
            work(d)
        # still running, 3 days logged: spread over at least the planned days
        v = S.editor_value_detail(D(2030, 11, 1), D(2030, 11, 30))[self.u.pk]["films"][0]
        self.assertEqual(v["v"], round(18000 * len(nov) / len(plan)))
        for d in plan[len(nov):]:
            work(d)
        self.p.first_delivered_on = D(2030, 12, 5)
        self.p.save()
        extra = [D(2030, 12, 9), D(2030, 12, 10)]
        for d in extra:
            work(d, TaskStage.REVISIONS)
        total = len(plan) + 2
        nov_v = S.editor_value_detail(D(2030, 11, 1), D(2030, 11, 30))[self.u.pk]["films"][0]
        dec = S.editor_value_detail(D(2030, 12, 1), D(2030, 12, 31))[self.u.pk]
        dec_v = dec["films"][0]
        self.assertEqual(nov_v["v"], round(18000 * len(nov) / total))          # overrun lowers the per-day value
        self.assertAlmostEqual(nov_v["v"] + dec_v["v"], 18000, delta=1)
        self.assertEqual((dec_v["rd"], dec_v["worked"], dec_v["plan"], dec_v["over"]), (2.0, total, len(plan), 2.0))
        self.assertEqual(dec["over"], 2.0)
        # half a day on this film counts as half a day
        log = DayLog.objects.get(user=self.u, date=extra[1])
        other = Project.objects.create(couple="Other", deliverable="Reels", agency=self.p.agency, editor=self.u)
        TaskEntry.objects.create(daylog=log, project=other, stage=TaskStage.EDIT, hours=8)
        dec2 = S.editor_value_detail(D(2030, 12, 1), D(2030, 12, 31))[self.u.pk]
        f = next(x for x in dec2["films"] if x["p"].pk == self.p.pk)
        self.assertEqual(f["rd"], 1.5)
        # a paid revision charge adds to the film's value
        self.p.revision_charge = 3000
        self.p.save()
        both = S.editor_value_detail(D(2030, 11, 1), D(2030, 12, 31))[self.u.pk]
        self.assertAlmostEqual(next(x for x in both["films"] if x["p"].pk == self.p.pk)["v"], 21000, delta=1)

    def test_team_page_periods_and_cost(self):
        f = self.login("ag")
        for vp in ("", "fy", "all"):
            r = f.get(f"/team/?vp={vp}")
            for c in r.context["vcards"]:
                self.assertEqual(c["cost"], c["salary"] + c["extra"])
        self.assertContains(r, 'data-swap-id="value"')
        self.assertEqual(f.get("/team/?vp=fy").context["vperiod"], "fy")


class StartDateRequired(SeededTestCase):
    def test_every_way_of_adding_a_film_needs_a_start_date(self):
        from ops.models import START_REQUIRED
        f = self.login("ag")
        ag = Agency.objects.get(code="BRC")
        r = f.post("/projects/new/", {"couple": "No Start", "agency": ag.pk, "deliverables": ["Teaser"]})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Add the start date")
        self.assertFalse(Project.objects.filter(couple="No Start").exists())
        p = Project.objects.exclude(status__in=(Project.CLOSED, Project.ON_HOLD)).first()
        Project.objects.filter(pk=p.pk).update(start_date=timezone.localdate(), deadline=None)
        d = f.post(f"/projects/{p.pk}/status/", {"start_date": ""}, **FETCH).json()
        self.assertFalse(d["ok"])
        p.refresh_from_db()
        self.assertIsNotNone(p.start_date)
        # flagged on Projects and the Dashboard
        Project.objects.filter(pk=p.pk).update(start_date=None)
        r = f.get("/projects/?kind=nostart&view=list")
        self.assertContains(r, "No start date")
        self.assertIn(p, [j["p"] for j in r.context["jobs"]])
        self.assertGreaterEqual(f.get("/dashboard/").context["no_start"], 1)
        self.assertTrue(START_REQUIRED)


class DriveProjectSync(SeededTestCase):
    """Drives <-> films by couple name: pick films in a drive's Contents, and drives
    suggested while adding a film."""

    def setUp(self):
        from ops.models import HardDrive
        self.ag = Agency.objects.get(code="BRC")
        self.f = self.login("ag")
        self.t = Project.objects.create(couple="Kavya & Rohan", deliverable="Trailer", agency=self.ag,
                                        start_date=timezone.localdate())
        self.h = Project.objects.create(couple="Kavya & Rohan", deliverable="Highlight", agency=self.ag,
                                        start_date=timezone.localdate())
        self.HD = HardDrive

    def test_film_suggestions_by_couple(self):
        j = self.f.get("/suggest/films/?q=kavya rohan").json()["films"]
        self.assertEqual({x["id"] for x in j}, {self.t.pk, self.h.pk})
        self.assertEqual(self.f.get("/suggest/films/?q=k").json()["films"], [])
        self.assertNotEqual(self.login("harsh").get("/suggest/films/?q=kavya").status_code, 200)

    def test_receive_and_edit_link_films(self):
        r = self.f.post("/drives/", {"code": "HDD-91", "kind": "HDD", "agency": self.ag.pk, "received_on": timezone.localdate(),
                                     "received_via": "courier", "status": "in_use", "contents": "",
                                     "films_link": f"{self.t.pk},{self.h.pk}"}, HTTP_X_LIVE_PAGE="1")
        self.assertRedirects(r, "/drives/", fetch_redirect_response=False)       # stays on the register
        d = self.HD.objects.get(code="HDD-91")
        self.assertEqual(set(d.projects.values_list("pk", flat=True)), {self.t.pk, self.h.pk})
        self.assertEqual(d.contents, "")
        self.assertContains(self.f.get("/drives/"), "Kavya &amp; Rohan – Highlight, Trailer")
        # edit: the Highlight is taken off the box -> unlinked; contents note kept
        r = self.f.get(f"/drives/{d.pk}/")
        self.assertContains(r, f"&quot;id&quot;: {self.h.pk}")                  # chips start with linked films
        self.f.post(f"/drives/{d.pk}/", {"code": "HDD-91", "kind": "HDD", "agency": self.ag.pk, "received_on": timezone.localdate(),
                                         "received_via": "courier", "status": "in_use", "contents": d.contents,
                                         "films_link": str(self.t.pk)})
        self.h.refresh_from_db()
        self.assertIsNone(self.h.drive_id)
        # no JS (no films_link sent): links untouched
        self.f.post(f"/drives/{d.pk}/", {"code": "HDD-91", "kind": "HDD", "agency": self.ag.pk, "received_on": timezone.localdate(),
                                         "received_via": "courier", "status": "in_use", "contents": "x"})
        self.t.refresh_from_db()
        self.assertEqual(self.t.drive_id, d.pk)

    def test_footage_suggested_when_adding_a_film(self):
        from ops.models import DriveFolder
        d = self.HD.objects.create(code="HDD-92", agency=self.ag)
        f = DriveFolder.objects.create(drive=d, path="E:/Kavya_Rohan_Wedding", name="Kavya_Rohan_Wedding",
                                       couple_guess="Kavya & Rohan", films_guess="Teaser")
        e = self.HD.objects.create(code="HDD-93", agency=self.ag)
        self.t.drive = e
        self.t.save()
        self.HD.objects.create(code="HDD-94", agency=self.ag, contents="Kavya Rohan haldi", status="sent")   # gone back: not offered
        j = self.f.get("/suggest/footage/?q=Kavya %26 Rohan").json()["drives"]
        self.assertEqual([x["code"] for x in j], ["HDD-92", "HDD-93"])
        self.assertEqual((j[0]["folder"], j[0]["films"]), (f.pk, ["Teaser"]))
        # picking the folder's drive: the new film gets that footage and the folder is decided
        r = self.f.post("/projects/new/", {"start_date": str(timezone.localdate()), "couple": "Kavya & Rohan",
                                           "agency": self.ag.pk, "deliverables": ["Teaser"], "source": str(d.pk),
                                           "folder": f.pk}, HTTP_X_LIVE_PAGE="1")
        self.assertRedirects(r, "/projects/new/", fetch_redirect_response=False)
        f.refresh_from_db()
        self.assertEqual(f.status, DriveFolder.ACCEPTED)
        self.assertEqual(Project.objects.get(couple="Kavya & Rohan", deliverable="Teaser").drive_id, d.pk)


class EditorsSeeTheirDrive(SeededTestCase):
    """Editors see which drive their film is on, with its photos (view only)."""

    def test_editor_sees_drive_and_photos_of_their_film_only(self):
        from django.contrib.auth import get_user_model
        from ops.models import HardDrive, StoredImage
        harsh = get_user_model().objects.get(username="harsh")
        ag = Agency.objects.get(code="BRC")
        d = HardDrive.objects.create(code="HDD-55", agency=ag, location="Shelf B")
        other = HardDrive.objects.create(code="HDD-56", agency=ag)
        img = StoredImage.objects.create(data=b"\xff\xd8\xff", content_type="image/jpeg")
        img2 = StoredImage.objects.create(data=b"\xff\xd8\xff", content_type="image/jpeg")
        d.photos.add(img)
        other.photos.add(img2)
        Project.objects.create(couple="Drive View", deliverable="Teaser", agency=ag, editor=harsh, drive=d,
                               start_date=timezone.localdate())
        c = self.login("harsh")
        r = c.get("/my-jobs/")
        self.assertContains(r, "HDD-55")
        self.assertContains(r, "Shelf B")
        self.assertContains(r, f"/img/{img.pk}/")
        self.assertEqual(c.get(f"/img/{img.pk}/").status_code, 200)
        self.assertEqual(c.get(f"/img/{img2.pk}/").status_code, 403)
        self.assertNotEqual(c.get(f"/drives/{d.pk}/").status_code, 200)       # can't open or edit the drive


class PlannerClientRevision(SeededTestCase):
    def test_client_revision_films_offered_for_revisions(self):
        ag = Agency.objects.get(code="BRC")
        cr = Project.objects.create(couple="Back Again", deliverable="Trailer", agency=ag, status=Project.CLIENT_REV,
                                    start_date=timezone.localdate())
        r = self.login("ag").get("/planner/")
        self.assertEqual(r.context["delivered"][0], cr)          # needing changes now: first
        self.assertContains(r, f'data-v="r:{cr.pk}"')
        self.assertContains(r, "client revision")


class HandOver(SeededTestCase):
    """A junior starts a film, a senior takes it over: each keeps the days they had it."""

    def setUp(self):
        from datetime import date
        from django.contrib.auth import get_user_model
        U = get_user_model()
        self.jr, self.sr = U.objects.get(username="soham"), U.objects.get(username="pratik")
        self.D = date
        self.ag = Agency.objects.get(code="BRC")

    def film(self, **kw):
        return Project.objects.create(couple="Hand Over", deliverable="Trailer", agency=self.ag, invoice_amount=18000,
                                      editor=self.jr, **kw)

    def test_value_split_between_editors(self):
        D = self.D
        p = self.film(start_date=D(2030, 11, 27), deadline=D(2030, 12, 5))
        p = Project.objects.get(pk=p.pk)
        p._handover_on, p._handover_force = D(2030, 12, 1), True
        p.editor = self.sr
        p.save()
        self.assertEqual(S.editor_on(p, D(2030, 11, 28)), self.jr.pk)
        self.assertEqual(S.editor_on(p, D(2030, 12, 2)), self.sr.pk)
        v = S.editor_value_detail(D(2030, 11, 1), D(2030, 12, 31))
        jr = next(f for f in v[self.jr.pk]["films"] if f["p"].pk == p.pk)
        sr = next(f for f in v[self.sr.pk]["films"] if f["p"].pk == p.pk)
        nov = len(S.leave_working_days(self.jr, D(2030, 11, 27), D(2030, 11, 30)))
        self.assertEqual(jr["days"], nov)
        self.assertAlmostEqual(jr["v"] + sr["v"], 18000, delta=1)

    def test_inline_change_after_work_is_a_handover_before_work_a_swap(self):
        from datetime import time
        from ops.models import DayLog, TaskEntry
        today = timezone.localdate()
        f = self.login("ag")
        swap = self.film(start_date=today)
        d = f.post(f"/projects/{swap.pk}/status/", {"editor": self.sr.pk}, **FETCH).json()
        self.assertFalse(swap.spells.exists())
        self.assertNotIn("handed over", d["messages"][0]["text"])
        p = self.film(start_date=today - timedelta(days=5))
        log, _ = DayLog.objects.get_or_create(user=self.jr, date=today - timedelta(days=2),
                                              defaults={"day_type": DayLog.OFFICE, "time_in": time(10), "time_out": time(19)})
        TaskEntry.objects.create(daylog=log, project=p, stage="Edit", hours=6)
        d = f.post(f"/projects/{p.pk}/status/", {"editor": self.sr.pk}, **FETCH).json()
        self.assertIn("handed over", d["messages"][0]["text"])
        self.assertEqual([(h["user"], h["end"]) for h in S.editor_history(p)],
                         [(self.jr, today - timedelta(days=1)), (self.sr, None)])
        self.assertContains(f.get("/projects/?view=list&q=Hand+Over"), "👥 Soham")
        # undo (back to the junior): one editor again
        f.post(f"/projects/{p.pk}/status/", {"editor": self.jr.pk}, **FETCH)
        self.assertEqual(S.editor_history(p), [])

    def edit_data(self, f, p, **over):
        form = f.get(f"/projects/{p.pk}/").context["form"]
        data = {}
        for name, field in form.fields.items():
            v = form[name].value()
            if v is None or v is False:
                continue
            data[name] = v.isoformat() if hasattr(v, "isoformat") else v
        data.update(over)
        return {k: v for k, v in data.items() if v is not None}

    def test_two_editors_with_dates_on_the_film_form(self):
        today = timezone.localdate()
        p = self.film(start_date=today - timedelta(days=4), deadline=today + timedelta(days=10))
        f = self.login("ag")
        r = f.post(f"/projects/{p.pk}/", self.edit_data(
            f, p, editor=self.jr.pk, editor_from=str(today - timedelta(days=4)), editor_to=str(today - timedelta(days=1)),
            editor2=self.sr.pk, editor2_from=str(today), editor2_to=""))
        self.assertEqual(r.status_code, 302, r.context and r.context["form"].errors)
        p.refresh_from_db()
        self.assertEqual(p.editor, self.sr)                                  # has it today
        self.assertEqual(S.editor_on(p, today - timedelta(days=2)), self.jr.pk)
        page = f.get(f"/projects/{p.pk}/")
        self.assertEqual(page.context["form"].initial["editor2"], self.sr.pk)
        self.assertContains(f.get("/projects/?view=list&q=Hand+Over"), "👥 Soham")
        # the Planner has the senior from today, not the junior
        from ops.models import PlanEntry
        self.assertFalse(PlanEntry.objects.filter(project=p, user=self.jr, date__gte=today).exists())
        # both on the same days: shared
        f.post(f"/projects/{p.pk}/", self.edit_data(f, p, editor=self.jr.pk, editor_from="", editor_to="",
                                                     editor2=self.sr.pk, editor2_from="", editor2_to=""))
        self.assertEqual(set(S.editors_on(p, today)), {self.jr.pk, self.sr.pk})
        # back to one editor
        f.post(f"/projects/{p.pk}/", self.edit_data(f, p, editor=self.jr.pk, editor_from="", editor_to="",
                                                     editor2="", editor2_from="", editor2_to=""))
        self.assertFalse(p.spells.exists())
        bad = f.post(f"/projects/{p.pk}/", self.edit_data(f, p, editor=self.jr.pk, editor2=self.jr.pk))
        self.assertContains(bad, "same person")

    def test_new_project_with_two_editors(self):
        today = timezone.localdate()
        f = self.login("ag")
        f.post("/projects/new/", {"couple": "Two Eds", "agency": self.ag.pk, "deliverables": ["Teaser"],
                                  "start_date": str(today), "deadline": str(today + timedelta(days=8)),
                                  "editor": self.jr.pk, "editor_to": str(today + timedelta(days=2)),
                                  "editor2": self.sr.pk, "editor2_from": str(today + timedelta(days=3))})
        p = Project.objects.get(couple="Two Eds")
        self.assertEqual(p.editor, self.jr)
        self.assertEqual(S.editor_on(p, today + timedelta(days=5)), self.sr.pk)


class DashboardMoneyHidden(SeededTestCase):
    def test_money_cards_marked_secret_with_an_eye(self):
        r = self.login("ag").get("/dashboard/")
        self.assertContains(r, "data-money-eye")
        self.assertContains(r, "data-secret", count=5)       # 4 money cards + the agencies panel (data-secret-part)


class EventWiseQC(SeededTestCase):
    """A full film sent for review one event at a time: Haldi in QC while Sangeet is being edited."""

    def test_event_goes_tl_then_qc_and_film_is_one_qc_entry(self):
        from django.contrib.auth import get_user_model
        from ops.models import FilmEvent, Review
        U = get_user_model()
        pratik = U.objects.get(username="pratik")
        p = Project.objects.create(couple="Event Wise", deliverable="Full Film", agency=Agency.objects.get(code="BRC"),
                                   editor=pratik, start_date=timezone.localdate(), status=Project.IN_PROGRESS)
        ed = self.login("pratik")
        self.assertContains(ed.get("/my-jobs/"), "Send event for TL review")
        ed.post(f"/my-jobs/{p.pk}/event/", {"event": "haldi", "cut_link": "https://frame.io/haldi"})
        ed.post(f"/my-jobs/{p.pk}/event/", {"event": "Sangeet", "cut_link": "https://frame.io/sangeet"})
        h = p.events.get(name="Haldi")
        self.assertEqual((h.status, p.events.count()), (FilmEvent.TL_REVIEW, 2))
        p.refresh_from_db()
        self.assertEqual((p.status, p.whole_review), (Project.TL_REVIEW, False))   # status follows the events
        self.assertTrue(p.can_submit)                                # the whole film can still be sent later
        r = ed.post(f"/my-jobs/{p.pk}/event/", {"event": "Haldi"}, follow=True)
        self.assertContains(r, "already waiting")
        # TL of the pod approves Haldi and Sangeet
        tl = self.login("nikhil")
        self.assertContains(tl.get("/pod/"), "Approve Haldi")
        for e in p.events.all():
            tl.post(f"/pod/event/{e.pk}/", {"decision": "pass", "comments": ""})
        p.refresh_from_db()
        self.assertEqual(p.status, Project.QC)                       # events in QC -> film In QC
        # QC: one entry for the film, events inside
        qc = self.login("qc")
        page = qc.get(f"/qc/?p={p.pk}")
        self.assertEqual([x.pk for x in page.context["queue"]].count(p.pk), 1)
        self.assertEqual(len(page.context["events"]), 2)
        self.assertContains(page, "2 events waiting")
        qc.post(f"/qc/event/{h.pk}/", {"decision": "pass", "comments": ""})
        s = p.events.get(name="Sangeet")
        qc.post(f"/qc/event/{s.pk}/", {"decision": "fail", "comments": "00:12 colour jump", "issue": ["2"]})
        h.refresh_from_db(); s.refresh_from_db()
        self.assertEqual((h.status, s.status), (FilmEvent.PASSED, FilmEvent.REVISIONS))
        p.refresh_from_db()
        self.assertEqual(p.status, Project.REVISIONS)                # nothing waiting, one sent back
        self.assertNotIn(p.pk, [x.pk for x in qc.get("/qc/").context["queue"]])
        self.assertTrue(Review.objects.filter(event=s, passed=False, kind="qc").exists())
        jobs = ed.get("/my-jobs/")
        self.assertContains(jobs, "Sangeet: QC sent it back")
        self.assertContains(jobs, "00:12 colour jump")
        # resend Sangeet after fixing
        ed.post(f"/my-jobs/{p.pk}/event/", {"event": "Sangeet"})
        s.refresh_from_db()
        self.assertEqual(s.status, FilmEvent.TL_REVIEW)
        # whole film sent while an event waits: it's a whole-film review too
        ed.post(f"/my-jobs/{p.pk}/submit/", {"cut_link": "https://frame.io/full"})
        p.refresh_from_db()
        self.assertEqual((p.status, p.whole_review), (Project.TL_REVIEW, True))


class QCEarnings(SeededTestCase):
    """QC closes films; a founder sets each film's QC fee; QC sees month and overall earnings."""

    def test_close_fee_earnings_and_founder_edits(self):
        from django.contrib.auth import get_user_model
        from ops.models import Review
        qcu = get_user_model().objects.get(username="qc")
        p = Project.objects.create(couple="QC Pay", deliverable="Teaser", agency=Agency.objects.get(code="BRC"),
                                   start_date=timezone.localdate(), status=Project.QC)
        qc = self.login("qc")
        self.assertNotContains(qc.get("/qc/"), "QC checklist")
        self.assertNotContains(qc.get("/qc/"), 'href="/leaderboard/"')
        r = qc.post(f"/qc/{p.pk}/close/", follow=True)
        self.assertContains(r, "checked in QC yet")
        qc.post(f"/qc/{p.pk}/", {"decision": "pass", "comments": ""})
        qc.post(f"/qc/{p.pk}/close/")
        p.refresh_from_db()
        self.assertEqual((p.qc_closed_on, p.qc_closed_by), (timezone.localdate(), qcu))
        page = qc.get("/qc/earnings/")
        self.assertContains(page, "fee not set yet")
        self.assertEqual(qc.post(f"/qc/{p.pk}/fee/", {"fee": "500"}).status_code, 403)   # only founders set fees
        ag = self.login("ag")
        d = ag.post(f"/qc/{p.pk}/fee/", {"fee": "750"}, **FETCH).json()
        self.assertTrue(d["ok"])
        self.assertEqual(qc.get("/qc/earnings/").context["total"], 750)
        self.assertEqual(qc.get("/qc/earnings/?v=all").context["total"], 750)
        self.assertContains(ag.get("/qc/"), "Ritika (QC): 1 film")
        # founder edits and deletes the QC entry, then removes the film from the list
        rev = Review.objects.get(project=p, kind="qc")
        ag.post(f"/qc/{p.pk}/entry/", {"op": "review_save", "review": rev.pk, "passed": "0", "comments": "fixed note"})
        rev.refresh_from_db()
        self.assertEqual((rev.passed, rev.comments), (False, "fixed note"))
        ag.post(f"/qc/{p.pk}/entry/", {"op": "closed", "qc_closed_on": "", "qc_closed_by": ""})
        p.refresh_from_db()
        self.assertIsNone(p.qc_closed_on)
        ag.post(f"/qc/{p.pk}/entry/", {"op": "remove"})
        self.assertFalse(p.reviews.filter(kind="qc").exists())
        self.assertEqual(qc.post(f"/qc/{p.pk}/entry/", {"op": "remove"}).status_code, 403)


class QCRemoveReallyRemoves(SeededTestCase):
    """A founder deleting a film's QC entries / removing it from the QC list must make it leave
    the board, even while the film (or one of its events) is waiting for QC."""

    def setUp(self):
        from ops.models import FilmEvent
        self.ag = self.login("ag")
        brc = Agency.objects.get(code="BRC")
        self.whole = Project.objects.create(couple="Waiting Whole", deliverable="Teaser", agency=brc,
                                            start_date=timezone.localdate(), status=Project.QC)
        self.byev = Project.objects.create(couple="Waiting Event", deliverable="Full Film", agency=brc,
                                           start_date=timezone.localdate(), status=Project.IN_PROGRESS)
        FilmEvent.objects.create(project=self.byev, name="Haldi", status=FilmEvent.QC)
        from ops.services import sync_film_status
        sync_film_status(self.byev)

    def board(self):
        return [b["p"].couple for b in self.ag.get("/qc/").context["board"]]

    def test_remove_takes_waiting_films_off_the_board(self):
        from ops.models import AutoCheckRun, FilmEvent, Review
        qc = self.login("qc")
        qc.post(f"/qc/{self.whole.pk}/", {"decision": "fail", "comments": "fix blacks"})
        Project.objects.filter(pk=self.whole.pk).update(status=Project.QC)       # editor re-sent it
        AutoCheckRun.objects.create(project=self.whole, round=2, auto=True)
        self.assertIn("Waiting Whole", self.board())
        self.assertIn("Waiting Event", self.board())
        r = self.ag.post(f"/qc/{self.whole.pk}/entry/", {"op": "remove"}, follow=True)
        self.assertContains(r, "now In progress")
        self.whole.refresh_from_db()
        self.assertEqual(self.whole.status, Project.IN_PROGRESS)
        self.assertFalse(Review.objects.filter(project=self.whole, kind="qc").exists())
        self.assertFalse(AutoCheckRun.objects.filter(project=self.whole, status=AutoCheckRun.QUEUED).exists())
        self.ag.post(f"/qc/{self.byev.pk}/entry/", {"op": "remove"})
        self.byev.refresh_from_db()
        self.assertEqual(self.byev.status, Project.IN_PROGRESS)
        self.assertFalse(FilmEvent.objects.filter(project=self.byev, status=FilmEvent.QC).exists())
        board = self.board()
        self.assertNotIn("Waiting Whole", board)
        self.assertNotIn("Waiting Event", board)

    def test_deleting_the_only_entry_of_a_passed_film_removes_it(self):
        from ops.models import Review
        qc = self.login("qc")
        qc.post(f"/qc/{self.whole.pk}/", {"decision": "pass", "comments": ""})
        self.assertIn("Waiting Whole", self.board())
        rev = Review.objects.get(project=self.whole, kind="qc")
        r = self.ag.post(f"/qc/{self.whole.pk}/entry/", {"op": "review_delete", "review": rev.pk}, follow=True)
        self.assertContains(r, "that QC entry is deleted")
        self.assertNotIn("Waiting Whole", self.board())

    def test_deleting_an_entry_while_waiting_says_why_it_stays(self):
        from ops.models import Review
        Review.objects.create(project=self.whole, kind=Review.QC, passed=False, comments="old")
        rev = Review.objects.get(project=self.whole, kind="qc")
        r = self.ag.post(f"/qc/{self.whole.pk}/entry/", {"op": "review_delete", "review": rev.pk}, follow=True)
        self.assertContains(r, "still waiting for QC")
        self.assertContains(r, "data-confirm=")


class QCContractMerged(SeededTestCase):
    def test_founder_menu_has_one_item_with_tabs(self):
        f = self.login("ag")
        r = f.get("/contract-editors/")
        labels = [i[0] for i in r.context["nav_items"]]
        self.assertIn("QC & contract editors", labels)
        self.assertNotIn("Contract editors", labels)
        self.assertNotIn("QC dashboard", labels)
        self.assertEqual([i[0] for i in r.context["nav_items"] if i[3]], ["QC & contract editors"])   # lit on both
        self.assertContains(r, 'aria-label="QC and contract editors"')
        self.assertContains(f.get("/qc/"), 'aria-label="QC and contract editors"')
        q = self.login("qc").get("/qc/")
        self.assertIn("QC dashboard", [i[0] for i in q.context["nav_items"]])
        self.assertNotContains(q, 'aria-label="QC and contract editors"')


class FounderSendsForQC(SeededTestCase):
    def test_founder_sends_whole_film_or_event(self):
        from ops.models import FilmEvent
        p = Project.objects.create(couple="Forgot To Send", deliverable="Full Film", agency=Agency.objects.get(code="BRC"),
                                   editor=None, start_date=timezone.localdate(),
                                   status=Project.IN_PROGRESS)
        f = self.login("ag")
        page = f.get("/qc/")
        self.assertContains(page, "Send a film for QC")
        self.assertNotContains(page, 'placeholder="Film link"')
        f.post("/qc/send/", {"project": p.pk, "event": "sangeet", "cut_link": "https://frame.io/s"})
        e = p.events.get(name="Sangeet")
        self.assertEqual((e.status, e.cut_link), (FilmEvent.QC, "https://frame.io/s"))
        f.post("/qc/send/", {"project": p.pk, "event": "", "cut_link": "https://frame.io/full"})
        p.refresh_from_db()
        self.assertEqual((p.status, p.cut_link), (Project.QC, "https://frame.io/full"))
        self.assertEqual(self.login("qc").post("/qc/send/", {"project": p.pk}).status_code, 403)
        bad = f.post("/qc/send/", {"project": p.pk, "cut_link": "not a link"}, follow=True)
        self.assertContains(bad, "Pick the film")              # already in QC: not offered any more


class AutoCheckPortal(SeededTestCase):
    """AutoCheck: the portal starts a run, stores progress and the report from the QC Helper,
    holds the rules and hands out the extension."""

    def setUp(self):
        self.p = Project.objects.create(couple="Auto Check", deliverable="Full Film", agency=Agency.objects.get(code="BRC"),
                                        start_date=timezone.localdate(), status=Project.QC)
        Project.objects.filter(pk=self.p.pk).update(whole_review=True)

    def test_run_lifecycle(self):
        import json
        from ops.models import AutoCheckRun
        qc = self.login("qc")
        page = qc.get(f"/qc/?p={self.p.pk}")
        self.assertContains(page, "data-autocheck")
        self.assertContains(page, "QC Helper isn")
        bad = qc.post(f"/qc/{self.p.pk}/autocheck/", {"v2_link": "https://example.com/x"}).json()
        self.assertFalse(bad["ok"])
        d = qc.post(f"/qc/{self.p.pk}/autocheck/", {"v1_link": "https://f.io/abc", "v2_link": "https://f.io/def"}).json()
        self.assertTrue(d["ok"])
        job = d["job"]
        self.assertEqual((job["v1"], job["v2"], job["checker"]), ("https://f.io/abc", "https://f.io/def", "Ritika (QC)"))
        self.assertTrue(job["rules"]["jump_cuts"])
        again = qc.post(f"/qc/{self.p.pk}/autocheck/", {"v2_link": "https://f.io/def"}).json()
        self.assertIn("already running", again["error"])
        same = qc.post(f"/qc/{self.p.pk}/autocheck/", {"v1_link": "https://f.io/x", "v2_link": "https://f.io/x"}).json()
        self.assertIn("same", same["error"])
        url = f"/qc/autocheck/{job['id']}/update/"
        qc.post(url, json.dumps({"type": "progress", "text": "Quick pass on V2…"}), content_type="application/json")
        run = AutoCheckRun.objects.get(pk=job["id"])
        self.assertEqual((run.status, run.progress), ("running", "Quick pass on V2…"))
        report = {"version": "1.0.0", "comments": [
            {"n": 1, "tc": "00:01:11:17", "text": "change the font", "verdict": "fixed", "note": "changed", "t2": 71.7},
            {"n": 14, "tc": "00:08:54:22", "text": "camera shake", "verdict": "not_fixed", "note": "same", "t2": 536},
            {"n": 17, "tc": "00:10:04:01", "text": "sound glitch", "verdict": "check", "note": "listen", "t2": 607}],
            "issues": [{"kind": "audio_peaks", "t": 617, "text": "Audio hits 0 dB."}],
            "posted": [{"t": 536, "text": "[AutoCheck] Pending from V1 comment 14: camera shake", "ok": True}],
            "structure": [{"kind": "added", "from": 366, "to": 381}], "seconds": 600}
        qc.post(url, json.dumps({"type": "done", "report": report, "posted": 3}), content_type="application/json")
        run.refresh_from_db()
        self.assertEqual(run.status, "done")
        self.assertEqual(run.counts, {"fixed": 1, "not_fixed": 1, "check": 1, "new": 1})
        rep = qc.get(f"/qc/autocheck/{run.pk}/")
        self.assertContains(rep, "Not fixed")
        self.assertContains(rep, "Added in V2 at 6:06–6:21")
        self.assertContains(qc.get(f"/qc/?p={self.p.pk}"), "Report →")
        # someone else can't write to this run
        self.assertEqual(self.login("pratik").post(url, "{}", content_type="application/json").status_code, 403)

    def test_editor_sends_queue_autocheck_with_versions(self):
        from django.contrib.auth import get_user_model
        from ops.models import AutoCheckRun, CutLink, FilmEvent
        pratik = get_user_model().objects.get(username="pratik")
        p = Project.objects.create(couple="Auto Queue", deliverable="Full Film", agency=Agency.objects.get(code="BRC"),
                                   editor=pratik, start_date=timezone.localdate(), status=Project.IN_PROGRESS)
        ed = self.login("pratik")
        ed.post(f"/my-jobs/{p.pk}/event/", {"event": "Haldi", "cut_link": "https://f.io/haldi1"})
        e = FilmEvent.objects.get(project=p, name="Haldi")
        r1 = AutoCheckRun.objects.get(project=p, event=e)
        self.assertEqual((r1.status, r1.auto, r1.round, r1.v1_link, r1.v2_link), ("queued", True, 1, "", "https://f.io/haldi1"))
        # QC sends it back, the editor sends the fixed version: V1 vs V2, and the old queued check is replaced
        FilmEvent.objects.filter(pk=e.pk).update(status=FilmEvent.REVISIONS)
        ed.post(f"/my-jobs/{p.pk}/event/", {"event": "Haldi", "cut_link": "https://f.io/haldi2"})
        r2 = AutoCheckRun.objects.filter(project=p, event=e).latest("pk")
        self.assertEqual((r2.round, r2.v1_link, r2.v2_link), (2, "https://f.io/haldi1", "https://f.io/haldi2"))
        r1.refresh_from_db()
        self.assertEqual((r1.status, r1.progress), ("failed", "Replaced by a newer link"))
        # the same link again (a version stacked in Frame.io): nothing new to compare
        CutLink.objects.filter(project=p).update(sent_at=timezone.now() - timezone.timedelta(hours=1))
        FilmEvent.objects.filter(pk=e.pk).update(status=FilmEvent.REVISIONS)
        ed.post(f"/my-jobs/{p.pk}/event/", {"event": "Haldi", "cut_link": "https://f.io/haldi2"})
        self.assertEqual(AutoCheckRun.objects.filter(project=p, event=e).count(), 2)
        # a Drive link is kept in the history but can't be AutoChecked
        ed.post(f"/my-jobs/{p.pk}/event/", {"event": "Sangeet", "cut_link": "https://drive.google.com/x"})
        self.assertTrue(CutLink.objects.filter(project=p, event__name="Sangeet").exists())
        self.assertFalse(AutoCheckRun.objects.filter(project=p, event__name="Sangeet").exists())
        # QC sees both versions on the event; the editor sees nothing of AutoCheck
        FilmEvent.objects.filter(pk=e.pk).update(status=FilmEvent.QC)
        page = self.login("qc").get(f"/qc/?p={p.pk}")
        self.assertContains(page, "https://f.io/haldi1")
        self.assertContains(page, ">V2</span>")
        self.assertContains(page, "Sent again with the same link")
        self.assertNotContains(ed.get("/my-jobs/"), "AutoCheck")
        self.assertNotContains(ed.get("/my-jobs/"), "ac-claim")

    def test_qc_browser_claims_queued_checks_one_at_a_time(self):
        from ops.models import AutoCheckRun
        from ops import services as S
        S.note_cut_link(self.p, None, "https://f.io/v1")
        S.note_cut_link(self.p, None, "https://f.io/v2")
        other = Project.objects.create(couple="Second", deliverable="Teaser", agency=Agency.objects.get(code="BRC"),
                                       start_date=timezone.localdate(), status=Project.QC)
        S.note_cut_link(other, None, "https://f.io/t1")
        qc = self.login("qc")
        self.assertContains(qc.get("/qc/"), 'name="ac-claim"')
        job = qc.post("/qc/autocheck/claim/").json()["job"]
        self.assertEqual((job["v1"], job["v2"], job["prev_label"], job["cur_label"], job["auto"], job["checker"]),
                         ("https://f.io/v1", "https://f.io/v2", "V1", "V2", True, "Ritika (QC)"))
        self.assertIsNone(qc.post("/qc/autocheck/claim/").json()["job"])         # one at a time
        # founders don't pick up queued checks by themselves (QC does), but can run one now
        self.assertIsNone(self.login("ag").post("/qc/autocheck/claim/").json()["job"])
        # a check that stopped responding goes back in the queue
        AutoCheckRun.objects.filter(pk=job["id"]).update(updated=timezone.now() - timezone.timedelta(hours=1))
        nxt = qc.post("/qc/autocheck/claim/").json()["job"]
        self.assertEqual(nxt["id"], job["id"])
        self.assertEqual(AutoCheckRun.objects.get(pk=job["id"]).attempts, 2)
        qc.post(f"/qc/autocheck/{job['id']}/update/", '{"type": "failed", "error": "x"}', content_type="application/json")
        third = qc.post("/qc/autocheck/claim/").json()["job"]
        self.assertEqual(third["v2"], "https://f.io/t1")
        self.assertEqual(third["cur_label"], "V1")

    def test_rules_pick_most_specific(self):
        from ops.models import AutoCheckRule
        f = self.login("ag")
        f.post("/qc/autocheck/rules/", {"deliverable": "", "sensitivity": "medium", "check_jump_cuts": "on"})
        f.post("/qc/autocheck/rules/", {"agency": self.p.agency_id, "deliverable": "Full Film", "sensitivity": "low",
                                         "min_seconds": "1200"})
        r = AutoCheckRule.for_project(self.p)
        self.assertEqual((r.sensitivity, r.min_seconds, r.check_jump_cuts), ("low", 1200, False))
        self.assertContains(f.get("/qc/autocheck/rules/"), "AutoCheck rules")
        self.assertEqual(self.login("qc").get("/qc/autocheck/rules/").status_code, 403)

    def test_helper_page_and_download(self):
        import io
        import zipfile
        qc = self.login("qc")
        self.assertContains(qc.get("/qc/helper/"), "Load unpacked")
        z = zipfile.ZipFile(io.BytesIO(qc.get("/qc/helper/download/").content))
        names = z.namelist()
        for f in ("manifest.json", "runner.js", "analyse.js", "checks.js", "fio.js", "bridge.js"):
            self.assertIn(f"RedWed-QC-Helper/{f}", names)
        self.assertNotEqual(self.login("pratik").get("/qc/helper/download/").status_code, 200)


class ExtraPaidWhenSent(SeededTestCase):
    """A fixed extra-work amount is earned once the film is sent to the agency; it doesn't
    have to be marked Delivered, and it counts once, in the month it was first sent."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.u = get_user_model().objects.get(username="pratik")
        self.p = Project.objects.create(couple="Sent Pay", deliverable="Teaser", agency=Agency.objects.get(code="BRC"),
                                        editor=self.u, is_extra=True, extra_basis="fixed", extra_rate=4000)

    def pay(self, month):
        from ops.perf import clear
        from ops.services import extra_pay
        clear()
        return extra_pay(self.u, month)

    def test_sent_to_agency_counts_in_my_earnings(self):
        from ops.services import add_months
        from ops.views.admin import apply_status
        today = timezone.localdate()
        month, last = today.replace(day=1), add_months(today.replace(day=1), -1)
        before = self.pay(month)
        r = self.login("pratik").get("/my-extra/")
        self.assertContains(r, "Coming when sent to the agency")
        self.assertContains(r, "Sent Pay · Teaser: ₹4,000")
        apply_status(self.p, Project.SENT_AGENCY, sent_on=today)
        self.assertEqual(self.pay(month), before + 4000)
        r = self.login("pratik").get("/my-extra/")
        self.assertNotContains(r, "Sent Pay · Teaser: ₹4,000")         # no longer "coming"
        self.assertContains(r, f"₹4,000 for the film, in {today:%B} earnings")
        # back with the client's changes, then delivered next month: still counted once, in the sent month
        apply_status(self.p, Project.CLIENT_REV)
        self.assertEqual(self.pay(month), before + 4000)
        self.p.status = Project.CLOSED
        self.p.closed_on = add_months(month, 1)
        self.p.save()
        self.assertEqual(self.pay(month), before + 4000)
        # a film sent last month is paid in last month's earnings, not this month's
        self.p.first_delivered_on = last
        self.p.save()
        self.assertEqual(self.pay(month), before)
        self.assertGreaterEqual(self.pay(last), 4000)

    def test_founder_extra_page_shows_payroll_month_once_sent(self):
        from ops.views.admin import apply_status
        apply_status(self.p, Project.SENT_AGENCY, sent_on=timezone.localdate())
        r = self.login("ag").get("/extra-work/")
        self.assertContains(r, f'data-extra-film="{self.p.pk}"')
        self.assertContains(r, f"{timezone.localdate():%b} payroll")


class EditorDeliveries(SeededTestCase):
    """Editors see films done this month, on time and late. Late = they logged work on the
    film after the editor's deadline (up to the day it was sent). The agency's deadline is ignored."""

    def setUp(self):
        from datetime import timedelta
        from django.contrib.auth import get_user_model
        self.u = get_user_model().objects.get(username="pratik")
        Project.objects.filter(editor=self.u).update(editor=None)          # start from a clean slate
        self.ag = Agency.objects.get(code="BRC")
        self.today = timezone.localdate()
        self.d = lambda n: self.today - timedelta(days=n)

    def film(self, name, deadline, sent, agency_deadline=None):
        p = Project.objects.create(couple=name, deliverable="Teaser", agency=self.ag, editor=self.u, deadline=deadline,
                                   agency_deadline=agency_deadline)
        if sent:
            p.status, p.first_delivered_on = Project.SENT_AGENCY, sent
            p.save()
        return p

    def log(self, p, day, stage=None):
        from ops.models import DayLog, TaskEntry, TaskStage
        log, _ = DayLog.objects.get_or_create(user=self.u, date=day, defaults={"day_type": DayLog.OFFICE})
        TaskEntry.objects.create(daylog=log, project=p, stage=stage or TaskStage.EDIT, hours=4)

    def test_on_time_late_and_overdue(self):
        from ops.models import TaskStage
        from ops.services import editor_deliveries
        if self.today.day < 4:
            self.skipTest("needs a few days of the month behind us")
        # worked only up to the deadline; agency's deadline long passed: still on time for the editor
        a = self.film("Ontime", self.d(2), self.d(1), agency_deadline=self.d(3))
        self.log(a, self.d(2))
        # worked the day after the editor's deadline: late, even though the agency's deadline is later
        b = self.film("Latefilm", self.d(3), self.d(1), agency_deadline=self.today)
        self.log(b, self.d(3))
        self.log(b, self.d(2))
        # client changes after the deadline never make it late
        c = self.film("Clientchg", self.d(3), self.d(1))
        self.log(c, self.d(2), stage=TaskStage.CLIENT)
        # work after sending (revisions) doesn't count either
        e = self.film("Aftersend", self.d(3), self.d(3))
        self.log(e, self.d(1))
        # still open and past the deadline
        self.film("Stillopen", self.d(1), None)
        r = editor_deliveries(self.u, self.today)
        self.assertEqual(r["done"], 4)
        self.assertEqual({p.couple for p in r["late"]}, {"Latefilm"})
        self.assertEqual({p.couple for p in r["on_time"]}, {"Ontime", "Clientchg", "Aftersend"})
        self.assertEqual(r["late"][0].days_over, 1)
        self.assertEqual(r["pct"], 75)
        self.assertEqual([p.couple for p in r["overdue"]], ["Stillopen"])

    def test_pages_show_the_boxes(self):
        if self.today.day < 4:
            self.skipTest("needs a few days of the month behind us")
        b = self.film("Latefilm", self.d(3), self.d(1))
        self.log(b, self.d(2))
        c = self.login("pratik")
        r = c.get("/today/")
        self.assertContains(r, "My deliveries")
        self.assertContains(r, "Delivered late")
        self.assertContains(r, "Latefilm · Teaser")
        r = c.get("/my-month/")
        self.assertContains(r, "Films done in")
        self.assertContains(r, "Late · 1 day")
        # someone else's films never show
        self.assertNotContains(self.login("nikhil").get("/my-month/"), "Latefilm")


class DeleteProjects(SeededTestCase):
    """Founders delete any film from Projects (row, couple view or the film's page); the work
    logged on it goes with it, invoices keep their lines, and nobody else can do it."""

    def setUp(self):
        from datetime import time, timedelta
        from django.contrib.auth import get_user_model
        from ops.models import DayLog, ExtraEntry, PlanEntry, TaskEntry
        self.ed = get_user_model().objects.get(username="pratik")
        self.brc = Agency.objects.get(code="BRC")
        t = timezone.localdate()
        self.p = Project.objects.create(couple="Delete Me", deliverable="Teaser", agency=self.brc, editor=self.ed,
                                        start_date=t, deadline=t + timedelta(days=5))
        self.q = Project.objects.create(couple="Delete Me", deliverable="Highlight", agency=self.brc, start_date=t)
        log, _ = DayLog.objects.get_or_create(user=self.ed, date=t - timedelta(days=1), defaults={"day_type": DayLog.OFFICE})
        TaskEntry.objects.create(daylog=log, project=self.p, hours=3)
        ExtraEntry.objects.create(user=self.ed, project=self.p, date=t, start=time(20), end=time(21))
        PlanEntry.objects.filter(user=self.ed, date=t + timedelta(days=1)).delete()
        PlanEntry.objects.create(user=self.ed, date=t + timedelta(days=1), project=self.p)
        self.f = self.login("ag")

    def test_delete_from_the_row(self):
        from ops.models import AuditEvent, Couple, TaskEntry
        r = self.f.get("/projects/?view=list&status=")
        self.assertContains(r, f'action="/projects/{self.p.pk}/delete/"')
        res = self.f.post(f"/projects/{self.p.pk}/delete/", {"next": "/projects/"}, **FETCH).json()
        self.assertTrue(res["ok"])
        self.assertEqual(res["remove"], [f'tr[data-pk="{self.p.pk}"]'])
        self.assertIn("1 logged day of work", res["messages"][0]["text"])
        self.assertIn("1 planned day", res["messages"][0]["text"])
        self.assertFalse(Project.objects.filter(pk=self.p.pk).exists())
        self.assertFalse(TaskEntry.objects.filter(project_id=self.p.pk).exists())
        self.assertTrue(Couple.objects.filter(films=self.q).exists())          # the couple still has a film
        self.assertTrue(AuditEvent.objects.filter(action="project.delete").exists())

    def test_invoice_keeps_its_line_and_couple_goes_with_last_film(self):
        from ops.models import Couple, Invoice, InvoiceLine
        inv = Invoice.objects.create(number="T-DEL-1", agency=self.brc)
        InvoiceLine.objects.create(invoice=inv, project=self.p, couple="Delete Me", film="Teaser", amount=15000)
        couple = self.p.client_id
        msg = self.f.post(f"/projects/{self.p.pk}/delete/", **FETCH).json()["messages"][0]["text"]
        self.assertIn("T-DEL-1", msg)
        line = InvoiceLine.objects.get(invoice=inv)
        self.assertIsNone(line.project_id)
        self.assertEqual(line.amount, 15000)
        self.f.post(f"/projects/{self.q.pk}/delete/", **FETCH)
        self.assertFalse(Couple.objects.filter(pk=couple).exists())

    def test_film_page_and_couple_delete(self):
        r = self.f.get(f"/projects/{self.p.pk}/")
        self.assertContains(r, "Delete this film")
        self.assertContains(r, "1 logged day of work on it")
        r = self.f.post(f"/projects/{self.p.pk}/delete/", follow=True)
        self.assertRedirects(r, "/projects/")
        self.assertFalse(Project.objects.filter(pk=self.p.pk).exists())
        # whole couple from the grouped view: needs the tick
        c = self.q.client_id
        res = self.f.post(f"/projects/couple/{c}/action/", {"op": "delete", "ids": [self.q.pk]}, **FETCH).json()
        self.assertFalse(res["ok"])
        self.assertTrue(Project.objects.filter(pk=self.q.pk).exists())
        res = self.f.post(f"/projects/couple/{c}/action/", {"op": "delete", "ids": [self.q.pk], "confirm": "yes"}, **FETCH).json()
        self.assertTrue(res["ok"])
        self.assertTrue(res["refresh"])
        self.assertFalse(Project.objects.filter(pk=self.q.pk).exists())

    def test_only_founders(self):
        for who in ("pratik", "nikhil", "qc", "drives"):
            self.assertNotEqual(self.login(who).post(f"/projects/{self.p.pk}/delete/").status_code, 302, who)
        self.assertTrue(Project.objects.filter(pk=self.p.pk).exists())
        self.assertEqual(self.f.get(f"/projects/{self.p.pk}/delete/").status_code, 302)     # GET deletes nothing
        self.assertTrue(Project.objects.filter(pk=self.p.pk).exists())


class AutoCheckLearning(SeededTestCase):
    """AutoCheck learns from what QC does with its [Hygiene] comments on the earlier version:
    deleted / "not an issue" = wrong, QC's own note or the editor's fix = right, QC's note where
    it flagged nothing = missed. Limits move only with enough evidence, in steps, and founders
    can undo or lock them."""

    def setUp(self):
        from ops.models import AutoCheckRun
        self.brc = Agency.objects.get(code="BRC")
        self.p = Project.objects.create(couple="Learn Me", deliverable="Highlight", agency=self.brc,
                                        start_date=timezone.localdate(), status=Project.QC)
        self.Run = AutoCheckRun
        self.n = 0

    def pair(self, issues, seen, cands=None, followup=None):
        """A finished check on V(n) with these flags posted, then the next check on V(n+1) that read V(n)'s comments."""
        self.n += 1
        a, b = f"https://f.io/v{self.n}a", f"https://f.io/v{self.n}b"
        posted = [{"t": x["t"], "text": "[Hygiene] " + x["text"], "ok": True, "issue": i, "kind": x["kind"]} for i, x in enumerate(issues)]
        prev = self.Run.objects.create(project=self.p, v2_link=a, status="done", report={
            "issues": issues, "posted": posted, "cands": cands or {}})
        run = self.Run.objects.create(project=self.p, v1_link=a, v2_link=b, status="done",
                                      report={"seen": seen, "followup": followup or [], "issues": []})
        return prev, run

    def test_judging_what_qc_did(self):
        from ops.learning import learn
        issues = [{"kind": "shake", "t": 10, "text": "Camera shake in this shot. Please stabilise.", "sev": 1.1},
                  {"kind": "blur", "t": 40, "text": "Shot looks out of focus (soft) here.", "sev": 1.3},
                  {"kind": "pops", "t": 70, "text": "Possible audio pop here, please listen.", "sev": 1.05},
                  {"kind": "colour_jump", "t": 100, "text": "Colour/exposure jump between shots at 1:40.", "sev": 1.2}]
        seen = [{"t": 10, "text": "[Hygiene] Camera shake in this shot. Please stabilise.", "ours": True},
                {"t": 11, "text": "yes stabilise this, very shaky", "ours": False},          # agrees -> right
                {"t": 40, "text": "[Hygiene] Shot looks out of focus (soft) here.", "ours": True},
                {"t": 41, "text": "intended, it's a dreamy shot", "ours": False},             # dismissed -> wrong
                # the pop comment was deleted by QC -> wrong
                {"t": 100, "text": "[Hygiene] Colour/exposure jump between shots at 1:40.", "ours": True},
                {"t": 200, "text": "this part is out of focus", "ours": False},                # missed, had a candidate
                {"t": 300, "text": "audio distorted here", "ours": False},                    # missed, too faint
                {"t": 400, "text": "change the font of the names", "ours": False}]            # no check for that
        prev, run = self.pair(issues, seen, cands={"blur": [[201, 0.8]]},
                              followup=[{"i": 3, "kind": "colour_jump", "still": False}])     # editor fixed it -> right
        s = learn(run)
        self.assertEqual((s["right"], s["wrong"], s["missed"], s["subtle"], s["notes"]), (2, 2, 1, 1, 1))
        why = {(w["kind"], w["real"]): w["why"] for w in s["why"]}
        self.assertEqual(why[("shake", True)], "QC noted the same thing")
        self.assertEqual(why[("blur", False)], "QC said it's not an issue")
        self.assertEqual(why[("pops", False)], "QC deleted the flag")
        self.assertEqual(why[("colour_jump", True)], "the editor fixed it")
        self.assertEqual(why[("blur", True)], "QC found one it saw but kept below its limit")
        prev.refresh_from_db()
        self.assertEqual(prev.report["feedback"]["run"], run.pk)
        self.assertIsNone(learn(run))                       # each earlier check teaches once

    def test_limit_moves_with_enough_evidence_and_is_used(self):
        from ops.learning import factors_for, learn, MIN_SAMPLES
        from ops.models import AutoCheckLesson, AutoCheckTuning
        for k in range(MIN_SAMPLES + 2):        # QC keeps deleting weak shake flags (strength 1.0–1.2)
            issues = [{"kind": "shake", "t": 10, "text": f"Camera shake {k}.", "sev": 1.0 + (k % 3) * 0.1},
                      {"kind": "shake", "t": 60, "text": f"Strong shake {k}.", "sev": 2.0}]
            seen = [{"t": 60, "text": f"[Hygiene] Strong shake {k}.", "ours": True},
                    {"t": 61, "text": "very shaky, stabilise", "ours": False}]
            learn(self.pair(issues, seen)[1])
        studio = AutoCheckTuning.objects.get(agency=None, deliverable="", kind="shake")
        self.assertGreater(studio.factor, 1.0)                      # flags shake less often now
        self.assertLessEqual(studio.factor, 1.25 * 1.25 * 1.25)    # in steps, never more than x1.25 at once
        self.assertLess(studio.factor, 2.0)                         # strong shakes still flagged
        self.assertEqual(studio.wrong, MIN_SAMPLES + 2)
        self.assertTrue(AutoCheckLesson.objects.filter(tuning=studio).exists())
        tune = factors_for(self.p)
        self.assertGreater(tune["shake"], 1.0)
        # the run hands the limits and the last flags to the helper
        qc = self.login("qc")
        d = qc.post(f"/qc/{self.p.pk}/autocheck/", {"v1_link": f"https://f.io/v{self.n}a", "v2_link": "https://f.io/next"}).json()
        self.assertGreater(d["job"]["rules"]["tune"]["shake"], 1.0)
        self.assertEqual([x["kind"] for x in d["job"]["prev_issues"]], ["shake", "shake"])
        # other agencies only get the studio-wide limit; this agency + film type learnt its own
        own = AutoCheckTuning.objects.get(agency=self.brc, deliverable="Highlight", kind="shake")
        self.assertAlmostEqual(own.factor, studio.factor)

    def test_misses_make_it_stricter(self):
        from ops.learning import learn, MIN_SAMPLES
        from ops.models import AutoCheckTuning
        for k in range(MIN_SAMPLES + 1):
            seen = [{"t": 30, "text": "this shot is out of focus", "ours": False}]
            learn(self.pair([], seen, cands={"blur": [[31, 0.7]]})[1])
        row = AutoCheckTuning.objects.get(agency=None, deliverable="", kind="blur")
        self.assertLess(row.factor, 1.0)
        self.assertEqual(row.missed, MIN_SAMPLES + 1)

    def test_founder_page_undo_lock_reset(self):
        from ops.learning import learn, MIN_SAMPLES
        from ops.models import AutoCheckLesson, AutoCheckTuning
        for k in range(MIN_SAMPLES + 1):
            learn(self.pair([], [{"t": 30, "text": "shot is blurry", "ours": False},
                                 {"t": 90, "text": "spelling of the names", "ours": False}], cands={"blur": [[30, 0.6]]})[1])
        f = self.login("ag")
        page = f.get("/qc/autocheck/learnt/")
        self.assertContains(page, "Out-of-focus shots")
        self.assertContains(page, "flags more often")
        self.assertContains(page, "Titles, names and spelling")
        row = AutoCheckTuning.objects.get(agency=None, deliverable="", kind="blur")
        les = AutoCheckLesson.objects.filter(tuning=row).first()
        f.post("/qc/autocheck/learnt/", {"op": "undo", "lesson": les.pk})
        row.refresh_from_db()
        self.assertEqual(row.factor, les.old_factor)
        self.assertTrue(row.locked)                         # undone limits stay put
        f.post("/qc/autocheck/learnt/", {"op": "lock", "pk": row.pk})
        row.refresh_from_db()
        self.assertFalse(row.locked)
        f.post("/qc/autocheck/learnt/", {"op": "reset", "pk": row.pk})
        row.refresh_from_db()
        self.assertEqual((row.factor, row.samples), (1.0, []))
        self.assertEqual(self.login("qc").get("/qc/autocheck/learnt/").status_code, 403)

    def test_report_from_helper_triggers_learning(self):
        import json
        prev, run = self.pair([{"kind": "flash", "t": 5, "text": "Flash frame at 0:05.", "sev": 1.2}],
                              [{"t": 20, "text": "nothing here", "ours": False}])
        from django.contrib.auth import get_user_model
        run.status, run.started_by = "running", get_user_model().objects.get(username="qc")
        run.save()
        qc = self.login("qc")
        rep = {"version": "1.3.0", "issues": [], "seen": [{"t": 6, "text": "flash frame here, remove", "ours": False}],
               "followup": [], "comments": [], "posted": []}
        qc.post(f"/qc/autocheck/{run.pk}/update/", json.dumps({"type": "done", "report": rep}), content_type="application/json")
        run.refresh_from_db()
        self.assertEqual(run.report["learnt"]["right"], 1)
        self.assertIn("learnt from QC", run.progress)
        self.assertContains(qc.get(f"/qc/autocheck/{run.pk}/"), "Learnt from QC")

    def test_keywords(self):
        from ops.learning import flag_of
        for text, kind in [("Media offline at 2:10", "media_offline"), ("black bars on this clip", "framing"),
                           ("soft focus, use another take", "blur"), ("music stops abruptly at the end", "music_end"),
                           ("voice is distorted", "clipping"), ("blacks are crushed", "blacks"), ("too shaky", "shake"),
                           ("change the song", None)]:
            self.assertEqual(flag_of(text), kind, text)


class HelperChecksJS(SeededTestCase):
    def test_hygiene_checks_in_node(self):
        import shutil
        import subprocess
        from pathlib import Path
        node = shutil.which("node")
        if not node:
            self.skipTest("node isn't installed")
        script = Path(__file__).resolve().parent / "js" / "test_checks.js"
        r = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class QCLinksStayVisible(SeededTestCase):
    """After QC passes or fails a film, its review links (V1, V2…) stay on the QC page: on the
    board and when the film is opened, though it's no longer in the queue."""

    def test_links_after_pass_and_fail(self):
        from ops import services as S
        p = Project.objects.create(couple="Links Stay", deliverable="Teaser", agency=Agency.objects.get(code="BRC"),
                                   start_date=timezone.localdate(), status=Project.QC, cut_link="https://f.io/teaser-v2")
        Project.objects.filter(pk=p.pk).update(whole_review=True)
        S.note_cut_link(p, None, "https://f.io/teaser-v1")
        S.note_cut_link(p, None, "https://f.io/teaser-v2")
        qc = self.login("qc")
        qc.post(f"/qc/{p.pk}/", {"decision": "fail", "comments": "lift the blacks"})
        p.refresh_from_db()
        self.assertEqual(p.status, Project.REVISIONS)
        board = qc.get("/qc/")
        self.assertContains(board, 'href="https://f.io/teaser-v2"')          # the latest version on the board
        self.assertContains(board, "V2 ↗")
        self.assertContains(board, f'href="?p={p.pk}#qc-film"')
        page = qc.get(f"/qc/?p={p.pk}")
        self.assertEqual(page.context["sel"].pk, p.pk)
        self.assertFalse(page.context["waiting"])
        self.assertContains(page, "https://f.io/teaser-v1")                    # every version when opened
        self.assertContains(page, "https://f.io/teaser-v2")
        self.assertContains(page, "lift the blacks")
        self.assertContains(page, "Back to the queue")
        self.assertNotContains(page, f'action="/qc/{p.pk}/"')                 # nothing to pass or fail now
        # passed later: still there
        Project.objects.filter(pk=p.pk).update(status=Project.QC, whole_review=True)
        qc.post(f"/qc/{p.pk}/", {"decision": "pass", "comments": ""})
        page = qc.get(f"/qc/?p={p.pk}")
        self.assertContains(page, "https://f.io/teaser-v2")
        self.assertContains(page, "passed</b> by")
        # a film that never reached QC can't be opened this way
        other = Project.objects.create(couple="Never QC", deliverable="Teaser", agency=Agency.objects.get(code="BRC"),
                                       start_date=timezone.localdate())
        sel = qc.get(f"/qc/?p={other.pk}").context["sel"]
        self.assertNotEqual(sel and sel.pk, other.pk)

    def test_three_versions_all_listed(self):
        from ops import services as S
        from ops.models import FilmEvent
        p = Project.objects.create(couple="Three Vers", deliverable="Full Film", agency=Agency.objects.get(code="BRC"),
                                   start_date=timezone.localdate(), status=Project.IN_PROGRESS)
        e = FilmEvent.objects.create(project=p, name="Haldi", status=FilmEvent.REVISIONS)
        for n in (1, 2, 3):
            S.note_cut_link(p, e, f"https://f.io/haldi-{n}")
        qc = self.login("qc")
        e.status = FilmEvent.QC
        e.save()
        qc.post(f"/qc/event/{e.pk}/", {"decision": "pass", "comments": ""})
        e.refresh_from_db()
        self.assertEqual(e.status, FilmEvent.PASSED)
        page = qc.get(f"/qc/?p={p.pk}")
        for n in (1, 2, 3):
            self.assertContains(page, f"https://f.io/haldi-{n}")
        self.assertContains(page, "Haldi V3 ↗")


class QCBoardInPlaceAndAutoCheckPage(SeededTestCase):
    """A film on the QC board opens in place (its details come as a fragment); AutoCheck has its
    own page in the menu to check any film, with the QC Helper download."""

    def setUp(self):
        from ops import services as S
        from ops.models import FilmEvent
        self.p = Project.objects.create(couple="Fold Open", deliverable="Full Film", agency=Agency.objects.get(code="BRC"),
                                        start_date=timezone.localdate(), status=Project.REVISIONS)
        self.e = FilmEvent.objects.create(project=self.p, name="Sangeet", status=FilmEvent.REVISIONS)
        S.note_cut_link(self.p, self.e, "https://f.io/sangeet-1")
        S.note_cut_link(self.p, self.e, "https://f.io/sangeet-2")
        S.note_cut_link(self.p, None, "https://f.io/full-1")

    def test_board_row_opens_in_place(self):
        from ops.models import Review
        from django.contrib.auth import get_user_model
        Review.objects.create(project=self.p, event=self.e, kind=Review.QC, passed=False, comments="fix the jump at 2:10",
                              reviewer=get_user_model().objects.get(username="qc"))
        qc = self.login("qc")
        board = qc.get("/qc/")
        self.assertContains(board, f'data-qc-film="{self.p.pk}"')
        self.assertContains(board, f'data-url="/qc/film/{self.p.pk}/"')
        self.assertContains(board, "qcboard.js")
        frag = qc.get(f"/qc/film/{self.p.pk}/", HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(frag.status_code, 200)
        self.assertNotContains(frag, "<html")                      # a piece of the page, not a page
        for link in ("https://f.io/sangeet-1", "https://f.io/sangeet-2", "https://f.io/full-1"):
            self.assertContains(frag, link)
        self.assertContains(frag, "fix the jump at 2:10")
        self.assertContains(frag, "data-autocheck")
        self.assertEqual(self.login("pratik").get(f"/qc/film/{self.p.pk}/").status_code, 403)

    def test_autocheck_page_in_menu_and_any_film(self):
        qc = self.login("qc")
        menu = [i[0] for i in qc.get("/qc/").context["nav_items"]]
        self.assertIn("AutoCheck", menu)
        self.assertIn("AutoCheck", [i[0] for i in self.login("ag").get("/dashboard/").context["nav_items"]])
        self.assertNotIn("AutoCheck", [i[0] for i in self.login("pratik").get("/today/").context["nav_items"]])
        page = qc.get("/autocheck/")
        self.assertContains(page, "Check a film")
        self.assertContains(page, 'href="/qc/helper/download/"')
        self.assertContains(page, "QC Helper 1.3.0")
        self.assertContains(page, "Fold Open · Full Film")
        d = qc.get(f"/autocheck/links/?p={self.p.pk}").json()
        self.assertEqual((d["v1"], d["v2"], d["start"]), ("", "https://f.io/full-1", f"/qc/{self.p.pk}/autocheck/"))
        self.assertEqual([e["name"] for e in d["events"]], ["Sangeet"])
        d = qc.get(f"/autocheck/links/?p={self.p.pk}&event={self.e.pk}").json()
        self.assertEqual((d["v1"], d["v2"], d["v1_label"], d["v2_label"]), ("https://f.io/sangeet-1", "https://f.io/sangeet-2", "V1", "V2"))
        # running it from the page uses the same start as the QC page (any film, not only ones in QC)
        r = qc.post(f"/qc/{self.p.pk}/autocheck/", {"event": self.e.pk, "v1_link": d["v1"], "v2_link": d["v2"]}).json()
        self.assertTrue(r["ok"])
        self.assertContains(qc.get("/autocheck/"), "Running")
        self.assertEqual(self.login("pratik").get("/autocheck/").status_code, 403)
        self.assertEqual(self.login("pratik").get(f"/autocheck/links/?p={self.p.pk}").status_code, 403)
