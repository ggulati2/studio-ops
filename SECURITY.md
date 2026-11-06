# Security

## Reporting a problem

If you find a way to see or change data your role shouldn't reach, or anything else that looks like a security issue, **don't open a public issue**. Message the founders directly (Gourav Gulati), with the page, the role you were signed in as and the steps. We aim to reply within 2 working days.

## If a password or laptop may be compromised

1. **Team & attendance → the person → set a new password.** They'll be asked to choose their own at the next sign-in.
2. Untick **Active** to cut access straight away, if needed. Their history stays.
3. Check **Settings → Audit log** for that login name.
4. If a founder account or the server itself may be affected, change `SECRET_KEY` in Vercel → Settings → Environment Variables and redeploy. That signs everyone out. Then reset the database password in Supabase (Project Settings → Database) and update `DATABASE_URL` in Vercel and `SUPABASE_DB_URL` in GitHub. Change `CRON_SECRET` too.

## What's protected and how

See the *Security* section of [README.md](README.md).

## Supported versions

Only the current `main` branch is deployed and supported.
