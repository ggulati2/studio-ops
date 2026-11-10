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
