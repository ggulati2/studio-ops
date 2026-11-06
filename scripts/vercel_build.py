"""Vercel build step (runs after `pip install`, before the app goes live).

Vercel runs `collectstatic` itself. Here, for production deployments only:
  1. apply database migrations,
  2. create the shared cache table (login lockout counters),
  3. lock down Supabase's public Data API (row level security on every table),
  4. create/check the founder login,
  5. fail the deploy if Django's production safety checks find a problem.
Preview deployments skip all database work (they use a throwaway demo DB).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

env = os.environ.get("VERCEL_ENV", "")
if env != "production":
    print(f"[build] VERCEL_ENV={env or 'unset'}: no database changes.")
    sys.exit(0)

if not os.environ.get("DATABASE_URL"):
    sys.exit("[build] DATABASE_URL is not set for Production in Vercel. See DEPLOY.md, step 3.")

# Migrations run over the pooler's session mode (port 5432): DDL and advisory locks
# need a real session, which transaction mode (6543) doesn't give.
url = os.environ.get("DATABASE_URL_MIGRATIONS") or os.environ["DATABASE_URL"].replace(":6543/", ":5432/")
os.environ["DATABASE_URL"] = url

import django  # noqa: E402

django.setup()
from django.core.management import call_command  # noqa: E402

for cmd, kw in [("migrate", {"interactive": False}), ("createcachetable", {}), ("secure_supabase", {}),
                ("ensure_founder", {}), ("check", {"deploy": True, "fail_level": "ERROR"})]:
    print(f"[build] manage.py {cmd}")
    call_command(cmd, **kw)
print("[build] done")
