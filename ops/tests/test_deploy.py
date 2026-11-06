"""Hosting set-up: Vercel settings, Supabase lockdown, daily cron, backup file."""
import gzip
import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import override_settings

from ops.models import AuditEvent, HardDrive
from ops.services import store_image

from .base import SeededTestCase
from .test_flows import jpeg

ROOT = Path(settings.BASE_DIR)


def settings_with(env):
    """Import settings in a fresh interpreter with the given environment; return the DB config."""
    code = ("import json,os;os.environ['DJANGO_SETTINGS_MODULE']='config.settings';"
            "from django.conf import settings as s;d=s.DATABASES['default'];"
            "print(json.dumps({k:str(v) for k,v in d.items()}))")
    full = {k: v for k, v in os.environ.items() if not k.startswith(("VERCEL", "DATABASE_URL", "PREVIEW_"))}
    full.update({"SECRET_KEY": "x" * 50, "DEBUG": "0", **env})
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=full, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


class VercelSettings(unittest.TestCase):
    def test_supabase_pooler_turns_off_what_transaction_mode_cant_do(self):
        db = settings_with({"DATABASE_URL": "postgresql://postgres.ref:pw@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"})
        self.assertEqual(db["ENGINE"], "django.db.backends.postgresql")
        self.assertIn("'prepare_threshold': None", db["OPTIONS"])
        self.assertIn("'sslmode': 'require'", db["OPTIONS"])
        self.assertEqual(db["DISABLE_SERVER_SIDE_CURSORS"], "True")
        self.assertEqual(db["CONN_MAX_AGE"], "0")

    def test_previews_never_use_the_production_database(self):
        db = settings_with({"VERCEL_ENV": "preview",
                            "DATABASE_URL": "postgresql://postgres.ref:pw@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"})
        self.assertEqual(db["ENGINE"], "django.db.backends.sqlite3")
        self.assertTrue(db["NAME"].startswith("/tmp/"))

    def test_dependencies_match(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
        reqs = [l.strip() for l in (ROOT / "requirements.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
        self.assertEqual(sorted(pyproject), sorted(reqs))

    def test_vercel_config(self):
        cfg = json.loads((ROOT / "vercel.json").read_text())
        self.assertIn("/cron/daily/", [c["path"] for c in cfg["crons"]])
        self.assertIn("config/wsgi.py", cfg["functions"])


class Cron(SeededTestCase):
    @override_settings(CRON_SECRET="s3cret-value")
    def test_cron_needs_the_secret(self):
        self.assertEqual(self.client.get("/cron/daily/").status_code, 401)
        self.assertEqual(self.client.get("/cron/daily/", HTTP_AUTHORIZATION="Bearer wrong").status_code, 401)
        r = self.client.get("/cron/daily/", HTTP_AUTHORIZATION="Bearer s3cret-value")
        self.assertEqual(r.status_code, 200)

    @override_settings(CRON_SECRET="")
    def test_cron_closed_when_no_secret_configured(self):
        self.assertEqual(self.client.get("/cron/daily/", HTTP_AUTHORIZATION="Bearer ").status_code, 401)


class Backup(SeededTestCase):
    def test_backup_is_small_and_restorable(self):
        HardDrive.objects.first().photos.add(store_image(jpeg(400, 300)))
        r = self.login("ag").get("/settings/backup/")
        self.assertEqual(r["Content-Type"], "application/gzip")
        data = gzip.decompress(r.content)
        self.assertLess(len(r.content), 4_500_000)
        rows = json.loads(data)
        models = {x["model"] for x in rows}
        self.assertNotIn("ops.storedimage", models)
        self.assertNotIn("ops.auditevent", models)
        self.assertFalse(any("photos" in x["fields"] or "photo" in x["fields"] for x in rows))
        self.assertTrue(AuditEvent.objects.filter(action="backup.download").exists())


class SupabaseLockdown(SeededTestCase):
    def test_api_roles_cannot_read_tables(self):
        if connection.vendor != "postgresql":
            call_command("secure_supabase", stdout=open(os.devnull, "w"))   # no-op on SQLite
            self.skipTest("needs Postgres (runs in CI)")
        with connection.cursor() as c:
            c.execute("SET CONSTRAINTS ALL IMMEDIATE")   # flush the test transaction's deferred FK checks
            for role in ("anon", "authenticated"):
                c.execute(f"DO $$ BEGIN CREATE ROLE {role} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;")
                c.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
                c.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}")
        call_command("secure_supabase", stdout=open(os.devnull, "w"))
        with connection.cursor() as c:
            c.execute("SELECT has_table_privilege('anon', 'public.auth_user', 'SELECT'),"
                      " has_table_privilege('authenticated', 'public.ops_profile', 'SELECT')")
            self.assertEqual(c.fetchone(), (False, False))
            c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND NOT rowsecurity")
            self.assertEqual(c.fetchone()[0], 0)
