"""Query budgets: a page that suddenly needs hundreds of database queries (each one a
round-trip to Supabase) fails here, before it reaches the studio."""
from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ops.models import DayLog, Project, TaskEntry

from django.contrib.auth import get_user_model

from .base import SeededTestCase

U = get_user_model()

FOUNDER_PAGES = {
    "/dashboard/": 40, "/projects/": 20, "/projects/?status=": 20, "/planner/": 20, "/planner/?view=month": 20,
    "/team/": 35, "/payroll/": 20, "/deadlines/": 16, "/delivered/": 16, "/reports/": 12, "/invoices/": 18,
    "/delays/": 16, "/qc/": 18, "/leave/": 16, "/contract-editors/": 18, "/agencies/": 16,
}
EDITOR_PAGES = {"/today/": 30, "/my-month/": 28, "/my-jobs/": 32, "/my-leave/": 14, "/leaderboard/": 22}


class QueryBudgets(SeededTestCase):
    def count(self, client, url):
        client.get(url)                                     # warm caches the way a real visit would
        with CaptureQueriesContext(connection) as q:
            r = client.get(url)
        self.assertEqual(r.status_code, 200, url)
        return len(q.captured_queries)

    def test_founder_pages(self):
        c = self.login("ag")
        for url, budget in FOUNDER_PAGES.items():
            with self.subTest(url=url):
                self.assertLessEqual(self.count(c, url), budget)

    def test_editor_pages(self):
        c = self.login("pratik")
        for url, budget in EDITOR_PAGES.items():
            with self.subTest(url=url):
                self.assertLessEqual(self.count(c, url), budget)

    def test_queries_do_not_grow_with_more_films(self):
        """The heavy pages must cost the same with 40 more films and their logged hours."""
        c = self.login("ag")
        pages = ["/dashboard/", "/team/", "/planner/", "/projects/?status=", "/payroll/"]
        before = {u: self.count(c, u) for u in pages}
        pratik = U.objects.get(username="pratik")
        ag = Project.objects.first().agency
        today = timezone.localdate()
        for i in range(40):
            p = Project.objects.create(couple=f"Load {i}", deliverable="Teaser", agency=ag, editor=pratik,
                                       status=Project.CLOSED if i % 2 else Project.IN_PROGRESS,
                                       deadline=today + timedelta(days=i % 20), invoice_amount=10000)
            log, _ = DayLog.objects.get_or_create(user=pratik, date=today - timedelta(days=60 + i),
                                                  defaults={"day_type": DayLog.OFFICE})
            TaskEntry.objects.create(daylog=log, project=p)
        for u in pages:
            with self.subTest(url=u):
                self.assertLessEqual(self.count(c, u), before[u] + 2)


class ProjectsPaging(SeededTestCase):
    def test_show_more(self):
        ag = Project.objects.first().agency
        for i in range(110):
            Project.objects.create(couple=f"Bulk {i:03}", deliverable="Reels", agency=ag)
        c = self.login("ag")
        r = c.get("/projects/?status=")
        self.assertEqual(len(r.context["jobs"]), 100)
        self.assertContains(r, "data-live-more")
        total = r.context["total"]
        r2 = c.get("/projects/?status=&show=200")
        self.assertEqual(len(r2.context["jobs"]), min(200, total))


class StaticAndHeaders(SeededTestCase):
    def test_no_third_party_fonts_and_preload_hints(self):
        r = self.login("ag").get("/dashboard/")
        html = r.content.decode()
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertIn("Geist-Variable", html)
        self.assertEqual(r["Speculation-Rules"], '"/speculation-rules.json"')
        rules = self.client.get("/speculation-rules.json")
        self.assertEqual(rules["Content-Type"], "application/speculationrules+json")
        self.assertIn("/settings/backup/*", rules.content.decode())

    def test_session_not_written_on_every_click(self):
        c = self.login("pratik")
        c.get("/today/")
        with CaptureQueriesContext(connection) as q:
            c.get("/my-month/")
        writes = [x["sql"] for x in q.captured_queries if "django_session" in x["sql"] and x["sql"].startswith(("UPDATE", "INSERT"))]
        self.assertEqual(writes, [])
