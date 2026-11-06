"""Close Supabase's auto-generated REST/GraphQL API over the app's tables.

Supabase publishes every table in the `public` schema through its Data API to
the `anon` and `authenticated` roles. This app never uses that API (all access
goes through Django), so we:
  • turn on Row Level Security on every table with no policies (= deny all),
  • revoke the API roles' privileges on existing and future tables.
Django connects as the table owner, which RLS doesn't restrict, so the app is
unaffected. Safe to run repeatedly; does nothing on SQLite or plain Postgres.
"""
from django.core.management.base import BaseCommand
from django.db import connection

API_ROLES = ("anon", "authenticated")


class Command(BaseCommand):
    help = "Lock Supabase's Data API out of the app's tables."

    def handle(self, *args, **opts):
        if connection.vendor != "postgresql":
            self.stdout.write("Not Postgres; nothing to do.")
            return
        with connection.cursor() as c:
            c.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", [list(API_ROLES)])
            roles = [r[0] for r in c.fetchall()]
            c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            tables = [r[0] for r in c.fetchall()]
            for t in tables:
                c.execute(f'ALTER TABLE public."{t}" ENABLE ROW LEVEL SECURITY')
            for role in roles:
                c.execute(f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "{role}"')
                c.execute(f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM "{role}"')
                c.execute(f'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM "{role}"')
                c.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM "{role}"')
                c.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM "{role}"')
                c.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM "{role}"')
        self.stdout.write(f"Row level security on {len(tables)} tables; API roles revoked: {', '.join(roles) or 'none present'}.")
