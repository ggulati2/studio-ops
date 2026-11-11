"""Projects grouped as Agency → Couple → Films."""
import json
from datetime import timedelta
from importlib import import_module

from django.apps import apps as django_apps
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

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
        d = self.act({"op": "add", "deliverable": "Highlight", "deadline": "", "agency_deadline": ""})
        self.assertTrue(d["ok"])
        p = Project.objects.get(client=self.c, deliverable="Highlight")
        self.assertEqual((p.couple, p.agency_id, p.invoice_amount), ("Meera & Dev", self.ag.pk, rate))
        bad = self.act({"op": "add", "deliverable": "Teaser", "deadline": "2030-02-10", "agency_deadline": "2030-02-01"})
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
        r = self.f.post(f"/projects/folders/{f.pk}/", {"op": "accept", "couple": "Rhea & Aryan",
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
        r = self.f.post(f"/projects/folders/{g.pk}/", {"op": "accept", "couple": "rhea and aryan",
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
        r = self.f.post("/projects/folders/", {"op": "accept", "ids": [a.pk, s.pk]}, **FETCH).json()
        self.assertTrue(Project.objects.filter(couple="Amit & Tanya", deliverable="Teaser").exists())
        self.assertIn("Sara weds Om", " ".join(m["text"] for m in r["messages"]))   # no films read: left for one-by-one
        self.f.post("/projects/folders/", {"op": "accept", "ids": [s.pk], "films": ["Full Film"]}, **FETCH)
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
        self.f.post(f"/projects/folders/{f.pk}/", {"op": "accept", "couple": "Amit & Tanya", "films": ["Teaser"]}, **FETCH)
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
