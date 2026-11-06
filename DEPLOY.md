# Deploy Studio Ops on Vercel + Supabase

About 30 minutes, once. You need three accounts: GitHub (already done), **Supabase** for the database and **Vercel** to run the app.

| Service | What it does | Limits that matter for the studio |
|---|---|---|
| **GitHub** | Holds the code (`ggulati2/studio-ops`), runs the tests on every change, and makes a weekly encrypted backup | Free for private repos |
| **Supabase** (Free) | Postgres database: all records and photos | **500 MB** database. **Paused after 1 week with no activity**: the app's daily job and the weekly backup both count as activity, so it stays awake. **No automatic backups on Free**, so the weekly GitHub backup covers that. |
| **Vercel** | Runs the app at `https://<name>.vercel.app` from the Mumbai region, with HTTPS and a global CDN for styles and images | **Hobby (free) is for personal, non-commercial use only.** A business tool like this needs **Pro, $20/month**. See the note below. |

> **Read before choosing a Vercel plan.** Vercel's fair-use guidelines say Hobby teams are "restricted to non-commercial personal use only" and that "all commercial usage of the platform requires either a Pro or Enterprise plan". Studio Ops runs Red Wed Films' paid work (invoices, payroll), so plan on **Vercel Pro ($20/month)**. If you're unsure, ask Vercel support. Supabase Free has no such rule.
>
> Limits checked in November 2026. Free tiers change: check vercel.com/pricing and supabase.com/pricing.

There's no wake-up delay to manage. Vercel starts the app on demand in about 1–2 seconds after a quiet spell, and it's instant once warm.

---

## 1. Create the database on Supabase (10 min)

1. Sign up at **supabase.com** (you can sign in with GitHub) → **New project**.
   - Name: `studio-ops`
   - Database password: click **Generate** and **save it in your password manager**.
   - Region: **South Asia (Mumbai)**, the same city as the app.
2. When it's ready, click **Connect** at the top.
   - Under **Transaction pooler** copy the URI. It ends in `:6543/postgres`. This is `DATABASE_URL`.
   - Under **Session pooler** copy the URI. It ends in `:5432/postgres`. This is for backups.
   - Replace `[YOUR-PASSWORD]` in both with the password from step 1. If the password has symbols like `@ # /`, generate one without them, or URL-encode it.
3. **Turn off the Data API**, because this app never uses it. Go to **Project Settings → Data API** and switch it off (or, if your dashboard only offers it, remove `public` from *Exposed schemas*).
   The app also locks every table on each deploy (row level security, API roles removed), but closing the door twice is better.

## 2. Create the app on Vercel (10 min)

1. Sign up at **vercel.com** **with GitHub** → **Add New → Project** → import `ggulati2/studio-ops`.
2. Vercel detects Django from `manage.py` and `pyproject.toml`. Leave the build settings as they are.
3. Before clicking Deploy, open **Environment Variables** and add:

| Name | Value | Environments |
|---|---|---|
| `SECRET_KEY` | A long random string: run `python -c "import secrets; print(secrets.token_urlsafe(60))"` or use any 60+ character password generator | Production **and** Preview |
| `DATABASE_URL` | The **Transaction pooler** URI (`…:6543/postgres`) | **Production only** |
| `FOUNDER_USERNAME` | Your login, e.g. `ag` | Production |
| `FOUNDER_PASSWORD` | A strong password (12+ characters) | Production |
| `CRON_SECRET` | Another long random string (Vercel sends it with the daily job) | Production |
| `TIME_ZONE` | `Asia/Kolkata` | Production and Preview |

Keep `DATABASE_URL` on **Production only**. Preview deployments (one per branch or pull request) then run on a private demo database with sample data and can never touch real studio records.

4. Click **Deploy**. The build installs everything and then, for production only:
   - applies the database tables
   - creates the shared login-lockout table
   - locks Supabase's API out of every table
   - creates your founder login
   - runs Django's production safety checks

   If any step fails, the old version stays live.
5. Open the address Vercel shows (e.g. `https://studio-ops.vercel.app`) and sign in.

*Forgot the founder password?* In Vercel → Settings → Environment Variables, change `FOUNDER_PASSWORD`, add `FOUNDER_RESET_PASSWORD` = `1`, then **Deployments → ⋯ → Redeploy**. Remove `FOUNDER_RESET_PASSWORD` afterwards.

## 3. Turn on the safety nets (5 min)

**In Vercel**
- **Settings → Deployment Protection:** keep **Vercel Authentication** on for Preview deployments, so only you can open branch previews (they use demo logins).
- **Settings → Functions:** the region should show **Mumbai (bom1)**, which `vercel.json` sets.

**In GitHub** (repo → Settings)
- **Secrets and variables → Actions → New repository secret:**
  - `SUPABASE_DB_URL` = the **Session pooler** URI (`…:5432/postgres`)
  - `BACKUP_PASSPHRASE` = a long passphrase. **Also save it in your password manager, because without it the backups can't be opened.**
- **Actions → Backup → Run workflow** once to test it. After that it runs every Sunday at 02:00 IST and keeps each backup for 90 days (Actions → Backup → a run → *Artifacts*).
- **Branches → Add rule for `main`:** require a pull request and the **CI / test** check. Vercel deploys whatever lands on `main`, so this is what stops a broken change going live.

## 4. First-time setup inside the app (15 min)

1. **Settings:**
   - Studio details, PAN, bank account, IFSC, bank name, invoice email and GST %.
   - Leave policy (2 free leaves, how a day's pay is worked out) and shift start time.
   - **Studio holidays** for the year.
2. **Agencies:** add each client once, with billing address, invoice emails, currency, payment terms and **usual rates** (e.g. `Trailer = 18000`).
3. **Team & attendance → Add a person:** add each person with their role (Team lead, Editor, QC, Drive desk), pod and salary. Set a first password for each.
4. **Pods:** in **Back office → Pods**, create Pod 1/2/3 and set each lead.
5. Send each person the link, their login name and the first password. The app makes them choose their own password the first time they sign in.

## Every week / month

- **Weekly, automatic:** encrypted full backup in GitHub. Glance at Actions → Backup now and then to see it's green.
- **Any time:** **Settings → Download backup** gives a quick copy of all records, without photos.
- **Monthly (2 min):**
  - **Settings → Audit log → Sign-ins, access and people:** look for repeated lockouts or blocked pages.
  - Deactivate anyone who has left.
  - Check Supabase → **Database size** (limit 500 MB).

## Restoring a backup

```bash
gpg -d studio-ops-2026-11-08.dump.gpg > db.dump          # asks for BACKUP_PASSPHRASE
pg_restore --clean --if-exists --no-owner -d "<Session pooler URI>" db.dump
```

## Updating the app later

Changes go through GitHub (see CONTRIBUTING.md): a branch, then a pull request with a demo preview link from Vercel, then green tests, then merge. Merging to `main` deploys to production, and database changes are applied during the build.

## When you outgrow free

| Symptom | Upgrade |
|---|---|
| Database close to 500 MB, or you want daily backups with point-in-time restore | Supabase **Pro** (from $25/month; more storage and managed daily backups) |
| More than a few people uploading many drive photos | Same as above; photos are compressed to roughly 150–300 KB each |

Nothing in the code changes for either upgrade.
