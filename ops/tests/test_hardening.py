"""Security review fixes (November 2026)."""
import time

from django.contrib.auth import get_user_model
from django.test import Client

from ops import twofactor as T
from ops.models import AuditEvent, PayrollMonth, Payslip, Project, TwoFactor
from ops.services import is_company_drive_link, safe_web_link

from .base import PW, SeededTestCase

U = get_user_model()


class BackOfficeLogin(SeededTestCase):
    def test_backoffice_login_goes_through_the_throttled_login(self):
        c = Client()
        r = c.get("/backoffice/login/?next=/backoffice/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith("/login/?next="))
        r = c.post("/backoffice/login/", {"username": "ag", "password": PW})
        self.assertNotIn("_auth_user_id", c.session)              # no sign-in through the back door
        for i in range(9):
            c.post("/login/", {"username": "ag", "password": f"wrong{i}"})
        self.assertEqual(c.post("/login/", {"username": "ag", "password": PW}).status_code, 429)


class PayrollNeedsPost(SeededTestCase):
    def test_a_link_cannot_change_payroll(self):
        c = self.login("ag")
        before = (PayrollMonth.objects.count(), Payslip.objects.count())
        self.assertEqual(c.get("/payroll/save/").status_code, 302)
        self.assertEqual((PayrollMonth.objects.count(), Payslip.objects.count()), before)


class Links(SeededTestCase):
    def test_drive_check_uses_the_host_name(self):
        self.assertTrue(is_company_drive_link("https://drive.google.com/drive/folders/abc"))
        for bad in ("https://evil.site/?drive.google.com", "https://drive.google.com.evil.site/x",
                    "javascript:alert(1)//drive.google.com", "http://drive.google.com/x", ""):
            self.assertFalse(is_company_drive_link(bad), bad)

    def test_review_link_must_be_a_web_address(self):
        self.assertIsNone(safe_web_link("javascript:alert(document.cookie)"))
        self.assertIsNone(safe_web_link("data:text/html,hi"))
        self.assertEqual(safe_web_link(" https://frame.io/x "), "https://frame.io/x")
        p = Project.objects.filter(editor__username="pratik", status__in=Project.WORKABLE).first()
        c = self.login("pratik")
        c.post(f"/my-jobs/{p.pk}/submit/", {"cut_link": "javascript:alert(1)"})
        p.refresh_from_db()
        self.assertNotIn("javascript", p.cut_link)
        self.assertIn(p.status, Project.WORKABLE)                  # not sent on with a bad link

    def test_no_redirect_off_site(self):
        c = self.login("ag")
        pr = U.objects.get(username="pratik")
        for evil in ("https://evil.site/", "//evil.site/", "/\\evil.site/"):
            r = c.post("/planner/", {"user": pr.pk, "date": "2026-12-01", "value": "", "back": evil})
            self.assertFalse(r["Location"].startswith(("http", "//", "/\\")), evil)


class StaffSessions(SeededTestCase):
    def test_editor_signed_out_after_a_working_day_idle(self):
        c = self.login("pratik")
        s = c.session
        s["last_seen"] = int(time.time()) - 13 * 3600
        s.save()
        r = c.get("/today/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("timeout=1", r["Location"])


class TwoStep(SeededTestCase):
    def enable(self, c):
        c.get("/account/two-step/")
        secret = c.session["2fa_setup"]
        r = c.post("/account/two-step/", {"action": "enable", "code": T._code(secret, int(time.time() // 30))})
        self.assertContains(r, "Save these recovery codes")
        return secret, r.context["codes"]

    def test_setup_then_sign_in_needs_the_code(self):
        c = self.login("ag")
        self.assertContains(c.get("/dashboard/"), "turn on two-step sign-in")       # founders are nudged
        secret, codes = self.enable(c)
        self.assertNotContains(c.get("/dashboard/"), "Protect the founder account")
        tf = TwoFactor.objects.get(user__username="ag")
        self.assertTrue(tf.enabled and len(tf.recovery_codes) == 10)
        self.assertNotIn(codes[0], str(tf.recovery_codes))                          # stored hashed

        n = Client()
        r = n.post("/login/", {"username": "ag", "password": PW})
        self.assertRedirects(r, "/login/code/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", n.session)                                # password alone isn't enough
        self.assertEqual(n.get("/dashboard/").status_code, 302)
        self.assertContains(n.post("/login/code/", {"code": "000000"}), "code isn")
        tf.refresh_from_db()
        good = T._code(secret, int(time.time() // 30) + 1)                         # next step, never used
        r = n.post("/login/code/", {"code": good})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(n.get("/dashboard/").status_code, 200)
        # the same code can't be replayed
        m = Client()
        m.post("/login/", {"username": "ag", "password": PW})
        self.assertContains(m.post("/login/code/", {"code": good}), "code isn")
        # a recovery code works once
        r = m.post("/login/code/", {"mode": "recovery", "code": codes[0]})
        self.assertEqual(r.status_code, 302)
        k = Client()
        k.post("/login/", {"username": "ag", "password": PW})
        self.assertContains(k.post("/login/code/", {"mode": "recovery", "code": codes[0]}), "already used")
        self.assertTrue(AuditEvent.objects.filter(action="auth.2fa_failed").exists())

    def test_too_many_wrong_codes_restart_sign_in(self):
        c = self.login("ag")
        self.enable(c)
        n = Client()
        n.post("/login/", {"username": "ag", "password": PW})
        for _ in range(4):
            n.post("/login/code/", {"code": "111111"})
        r = n.post("/login/code/", {"code": "111111"})
        self.assertRedirects(r, "/login/", fetch_redirect_response=False)
        self.assertNotIn("2fa", n.session)

    def test_founder_can_reset_someone_and_turn_off_needs_password(self):
        p = self.login("pratik")
        secret, _ = self.enable(p)
        r = p.post("/account/two-step/", {"action": "disable", "password": "wrong",
                                          "code": T._code(secret, int(time.time() // 30) + 1)})
        self.assertTrue(TwoFactor.objects.filter(user__username="pratik").exists())
        pr = U.objects.get(username="pratik")
        self.login("ag").post(f"/team/{pr.pk}/reset-two-step/")
        self.assertFalse(TwoFactor.objects.filter(user=pr).exists())
        self.assertEqual(self.login("pratik").post(f"/team/{pr.pk}/reset-two-step/").status_code, 403)

    def test_rfc6238_vector(self):
        import base64
        s = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
        self.assertEqual(T._code(s, 1), "287082")
