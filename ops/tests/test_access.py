"""Every page opens for the roles that should see it, and is blocked (403) for everyone else."""
from django.urls import URLPattern, URLResolver, get_resolver

from ops.models import HardDrive, Invoice, Project
from ops.rbac import ADMIN, PERMISSIONS, ROLE_PERMISSIONS, SENSITIVE

from .base import SeededTestCase


class PagesOpen(SeededTestCase):
    def test_every_role_opens_its_pages(self):
        pid, inv, dr = Project.objects.first().pk, Invoice.objects.first().pk, HardDrive.objects.first().pk
        qc_p = Project.objects.filter(status="qc").first().pk
        pages = {
            "ag": ["/", "/dashboard/", "/projects/", "/projects/new/", f"/projects/{pid}/", "/planner/", "/deadlines/",
                   "/deadlines/?view=client", "/delivered/", "/reports/", "/reports/?agency=1", "/reports/print/",
                   "/reports/print/?agency=1", "/invoices/", "/invoices/new/", "/invoices/new/?agency=1", f"/invoices/{inv}/",
                   "/team/", "/team/new/", "/team/2/", "/extra-work/", "/payroll/", "/trailblazer-of-the-month/", "/agencies/",
                   "/agencies/new/", "/agencies/1/", "/settings/", "/settings/audit/", "/settings/access/", "/drives/",
                   f"/drives/{dr}/", "/drives/send-back/", "/qc/", "/leaderboard/", "/backoffice/", "/password/"],
            "nikhil": ["/", "/pod/", "/today/", "/my-month/", "/my-jobs/", "/my-extra/", "/leaderboard/", "/my-payslips/"],
            "pratik": ["/", "/today/", "/today/?date=2026-11-01", "/my-month/", "/my-jobs/", "/my-extra/", "/leaderboard/",
                       "/my-payslips/"],
            "shabnam": ["/today/", "/leaderboard/"],
            "qc": ["/", "/qc/", f"/qc/?p={qc_p}", "/leaderboard/"],
            "drives": ["/", "/drives/", "/drives/send-back/", f"/drives/{dr}/"],
        }
        for user, urls in pages.items():
            c = self.login(user)
            for u in urls:
                with self.subTest(user=user, url=u):
                    self.assertEqual(c.get(u, follow=True).status_code, 200)

    def test_blocked_pages_return_403(self):
        inv = Invoice.objects.first().pk
        blocked = {
            "pratik": ["/dashboard/", "/invoices/", "/payroll/", "/team/", "/pod/", "/qc/", "/drives/", "/agencies/",
                       f"/invoices/{inv}/", "/reports/print/", "/settings/audit/", "/settings/"],
            "nikhil": ["/dashboard/", "/invoices/", "/payroll/", "/team/", "/drives/", "/settings/", "/settings/audit/"],
            "qc": ["/dashboard/", "/invoices/", "/payroll/", "/today/", "/drives/", "/my-payslips/"],
            "drives": ["/dashboard/", "/invoices/", "/payroll/", "/projects/", "/today/", "/qc/", "/team/", "/leaderboard/"],
        }
        for user, urls in blocked.items():
            c = self.login(user)
            for u in urls:
                with self.subTest(user=user, url=u):
                    self.assertEqual(c.get(u).status_code, 403)

    def test_signed_out_people_are_sent_to_login(self):
        r = self.client.get("/dashboard/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])
        self.assertEqual(self.client.get("/health/").status_code, 200)

    def test_menu_only_shows_allowed_pages(self):
        for user in ("pratik", "qc", "drives", "nikhil"):
            r = self.login(user).get("/", follow=True)
            html = r.content.decode()
            for href in ("/invoices/", "/payroll/", "/dashboard/", "/agencies/", "/settings/"):
                with self.subTest(user=user, href=href):
                    self.assertNotIn(f'href="{href}"', html)


class RbacTable(SeededTestCase):
    def test_private_permissions_belong_to_founders_only(self):
        for role, perms in ROLE_PERMISSIONS.items():
            if role != ADMIN:
                self.assertFalse(perms & SENSITIVE, role)

    def test_every_view_declares_its_guard(self):
        """No page can be added without a permission (except the few public/self-service ones)."""
        open_ok = {"login", "logout", "health", "home", "image", "password_change", "payslip", "cron_daily", "speculation_rules"}  # speculation_rules: static link-preload hints, no data; payslip: owner or payroll.manage, checked inside

        def walk(patterns, prefix=""):
            for p in patterns:
                if isinstance(p, URLResolver):
                    if "admin" in str(p.pattern) or "backoffice" in str(p.pattern):
                        continue
                    yield from walk(p.url_patterns)
                elif isinstance(p, URLPattern):
                    yield p
        for p in walk(get_resolver().url_patterns):
            with self.subTest(view=p.name):
                from django.views.generic import RedirectView
                if p.name in open_ok or getattr(p.callback, "view_class", None) is RedirectView:
                    continue   # plain redirects only forward to a guarded page
                perms = getattr(p.callback, "required_perms", None)
                self.assertTrue(perms, f"{p.name} has no @requires(...)")
                self.assertTrue(set(perms) <= set(PERMISSIONS))
