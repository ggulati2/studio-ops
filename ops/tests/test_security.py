"""Security controls: headers, lockout, forced password change, idle sign-out, uploads, back office, audit."""
import io
import re
import time
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from ops.models import AuditEvent, EmployeeOfMonth, HardDrive, Profile
from ops.services import store_image

from .base import PW, SeededTestCase
from .test_flows import jpeg

U = get_user_model()


class Headers(SeededTestCase):
    def test_security_headers_on_pages(self):
        r = self.login("pratik").get("/today/")
        csp = r["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertEqual(r["X-Frame-Options"], "DENY")
        self.assertIn("camera=()", r["Permissions-Policy"])
        self.assertIn("no-store", r["Cache-Control"])

    def test_no_inline_scripts_in_templates(self):
        """CSP forbids inline JS, so none may exist (behaviour lives in static/app.js)."""
        bad = re.compile(r"\son[a-z]+\s*=|javascript:|<script(?![^>]*\ssrc=)", re.I)
        for f in Path(settings.BASE_DIR, "templates").rglob("*.html"):
            with self.subTest(template=str(f)):
                self.assertIsNone(bad.search(f.read_text()), f)

    def test_production_settings_are_strict(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertIn("ScryptPasswordHasher", open(Path(settings.BASE_DIR, "config/settings.py")).read())


class Login(SeededTestCase):
    def setUp(self):
        cache.clear()

    def test_lockout_after_eight_wrong_passwords(self):
        c = Client()
        for _ in range(8):
            c.post("/login/", {"username": "pratik", "password": "wrong-one"})
        r = c.post("/login/", {"username": "pratik", "password": PW})
        self.assertEqual(r.status_code, 429)
        self.assertNotIn("_auth_user_id", c.session)
        self.assertEqual(AuditEvent.objects.filter(action="auth.login_failed", target="pratik").count(), 8)
        self.assertTrue(AuditEvent.objects.filter(action="auth.locked_out").exists())

    def test_good_login_is_audited(self):
        r = Client().post("/login/", {"username": "pratik", "password": PW})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(AuditEvent.objects.filter(action="auth.login", username="pratik").exists())

    def test_founder_set_password_must_be_changed(self):
        ag = self.login("ag")
        pr = U.objects.get(username="pratik")
        r = ag.post(f"/team/{pr.pk}/", {"first_name": "Pratik", "username": "pratik", "email": "", "role": "editor",
                                        "pod": pr.profile.pod_id or "", "monthly_salary": 35000, "active": "on",
                                        "password": "Temp-Pass-2026x"})
        self.assertEqual(r.status_code, 302)
        pr.profile.refresh_from_db()
        self.assertTrue(pr.profile.must_change_password)
        c = Client(); c.login(username="pratik", password="Temp-Pass-2026x")
        r = c.get("/today/")
        self.assertEqual(r.status_code, 302); self.assertIn("/password/", r["Location"])
        c.post("/password/", {"old_password": "Temp-Pass-2026x", "new_password1": "My-Own-Secret-77",
                              "new_password2": "My-Own-Secret-77"})
        pr.profile.refresh_from_db()
        self.assertFalse(pr.profile.must_change_password)
        self.assertEqual(c.get("/today/").status_code, 200)

    def test_weak_passwords_rejected(self):
        ag = self.login("ag")
        r = ag.post("/team/new/", {"first_name": "New", "username": "newbie", "role": "editor", "monthly_salary": 0,
                                   "active": "on", "password": "12345678"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(U.objects.filter(username="newbie").exists())

    def test_founder_signed_out_when_idle(self):
        c = self.login("ag")
        c.get("/dashboard/")
        s = c.session; s["last_seen"] = int(time.time()) - settings.ADMIN_IDLE_MINUTES * 60 - 5; s.save()
        r = c.get("/dashboard/")
        self.assertEqual(r.status_code, 302); self.assertIn("timeout=1", r["Location"])


class People(SeededTestCase):
    def _post(self, c, user, **kw):
        data = {"first_name": user.first_name or user.username, "username": user.username, "email": user.email,
                "role": user.profile.role, "pod": user.profile.pod_id or "", "monthly_salary": user.profile.monthly_salary,
                "active": "on", "password": ""}
        data.update(kw)
        return c.post(f"/team/{user.pk}/", data)

    def test_role_change_syncs_back_office_and_is_audited(self):
        ag, pr = self.login("ag"), U.objects.get(username="pratik")
        self._post(ag, pr, role="admin")
        pr.refresh_from_db(); self.assertTrue(pr.is_superuser and pr.is_staff)
        self._post(ag, pr, role="editor")
        pr.refresh_from_db(); self.assertFalse(pr.is_superuser or pr.is_staff)
        self.assertTrue(AuditEvent.objects.filter(action="people.update", target="pratik", detail__contains="admin -> editor").exists())

    def test_last_founder_cannot_be_removed(self):
        ag = U.objects.get(username="ag")
        Profile.objects.filter(role="admin").exclude(user=ag).update(role="editor")
        r = self._post(self.login("ag"), ag, role="editor")
        self.assertContains(r, "last active founder")
        ag.profile.refresh_from_db(); self.assertEqual(ag.profile.role, "admin")

    def test_back_office_is_founders_only(self):
        pr = U.objects.get(username="pratik"); pr.is_staff = True; pr.save()
        r = self.login("pratik").get("/backoffice/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.login("ag").get("/backoffice/").status_code, 200)

    def test_inactive_people_lose_access(self):
        pr = U.objects.get(username="pratik")
        c = self.login("pratik")
        pr.profile.active = False; pr.profile.save()
        self.assertEqual(c.get("/today/").status_code, 403)


class Uploads(SeededTestCase):
    def _drive(self, c, files):
        return c.post("/drives/", {"code": "HDD-77", "kind": "HDD", "contents": "x", "received_on": "2026-11-01",
                                   "received_via": "courier", "status": "in_use", "photos_upload": files})

    def test_fake_image_rejected(self):
        fake = SimpleUploadedFile("evil.jpg", b"<?php system($_GET['x']); ?>", content_type="image/jpeg")
        r = self._drive(self.login("drives"), [fake])
        self.assertEqual(r.status_code, 200)
        self.assertFalse(HardDrive.objects.filter(code="HDD-77").exists())

    def test_too_many_photos_rejected(self):
        r = self._drive(self.login("drives"), [jpeg(50, 50, f"{i}.jpg") for i in range(7)])
        self.assertContains(r, "Up to 6 photos")

    def test_photo_access_follows_role(self):
        drive_photo = store_image(jpeg(100, 100))
        HardDrive.objects.first().photos.add(drive_photo)
        self.assertEqual(self.login("drives").get(f"/img/{drive_photo.pk}/").status_code, 200)
        self.assertEqual(self.login("pratik").get(f"/img/{drive_photo.pk}/").status_code, 403)
        eotm = EmployeeOfMonth.objects.first()
        if eotm:
            eotm.photo = store_image(jpeg(100, 100)); eotm.save()
            self.assertEqual(self.login("pratik").get(f"/img/{eotm.photo.pk}/").status_code, 200)


class Audit(SeededTestCase):
    def test_changes_and_denials_are_logged(self):
        ag = self.login("ag")
        pr = U.objects.get(username="pratik")
        ag.post("/planner/", {"user": pr.pk, "date": "2026-12-01", "value": "Back-up"})
        self.assertTrue(AuditEvent.objects.filter(action="change.planner", username="ag").exists())
        self.login("pratik").get("/payroll/")
        self.assertTrue(AuditEvent.objects.filter(action="access.denied", username="pratik", target="/payroll/").exists())
        ag.get("/settings/backup/")
        self.assertTrue(AuditEvent.objects.filter(action="backup.download").exists())
        # never store secrets
        self.assertFalse(AuditEvent.objects.filter(detail__icontains="password=").exists())

    def test_audit_log_page_founders_only(self):
        self.assertEqual(self.login("ag").get("/settings/audit/?kind=security").status_code, 200)
        self.assertEqual(self.login("nikhil").get("/settings/audit/").status_code, 403)
