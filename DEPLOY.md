# Deploy Studio Ops for ₹0 (Render + Supabase)

About 30–40 minutes, once. No credit card needed.

| Service | What it does | Free limits that matter |
|---|---|---|
| **GitHub** | Holds the code (`ggulati2/studio-ops`), runs the tests on every change, makes a weekly encrypted backup | Free private repos |
| **Supabase** | The Postgres database: all records and photos | **500 MB**. **Paused after 1 week with no activity**: the daily job (step 4) and the weekly backup keep it active. **No automatic backups on Free**: the weekly GitHub backup covers that. |
| **Render** | Runs the app at `https://<name>.onrender.com` | One app 24/7 fits the free hours. **Sleeps after 15 min with no visits** and takes about 1 minute to wake, so the pinger in step 4 keeps it awake. No permanent disk, which is why data lives on Supabase. |
| **cron-job.org** | Two free scheduled calls: keep Render awake, and run the daily job | Free |

> **Know what free means on Render.** Its terms don't ban business use (Vercel's free plan does), but Render's docs say free instances are for hobby projects and testing, and "Do not use them for production applications". A free service can be restarted at any time, and it can be suspended if it makes unusually heavy calls to an outside database. A ~12-person studio is light traffic, so free is a reasonable way to start. When the studio relies on it every day, **Render Starter (about $7/month)** removes the sleep and the restart caveats. Nothing in the code changes.
>
> Free tiers change. These limits were checked in November 2026: see render.com/docs/free and supabase.com/pricing.

Both services run in **Singapore**. Render has no India region, and keeping the database next to the app matters more than either being close to Mumbai. Each page makes many small database queries, and a Mumbai database would add a round-trip to every one.

---

## 1. Create the database on Supabase (10 min)

1. Sign up at **supabase.com** (you can sign in with GitHub) → **New project**.
   - Name: `studio-ops`
   - Database password: click **Generate** and **save it in your password manager**. Generate one without `@ # / ?` so it can go straight into a link.
   - Region: **Southeast Asia (Singapore)**.
2. When it's ready, click **Connect** at the top → **Session pooler** → copy the URI. It looks like:
   `postgresql://postgres.abcd1234:[YOUR-PASSWORD]@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres`
   Put your password in place of `[YOUR-PASSWORD]`. This is your **`DATABASE_URL`**.
   *(Use the Session pooler, not "Direct connection". The direct one needs IPv6, which Render doesn't have.)*
3. **Turn off the Data API**, because this app never uses it: **Project Settings → Data API** → switch it off (or remove `public` from *Exposed schemas*).
   Each deploy also locks every table against it (row level security on, API roles removed). This step closes it at the front door too.

## 2. Deploy on Render (10 min)

1. Sign up at **render.com** **with GitHub** and allow access to the `studio-ops` repo.
2. **New → Blueprint** → pick the repo. Render reads `render.yaml` and asks for three values:
   - `DATABASE_URL`: the Supabase Session pooler URI from step 1.
   - `FOUNDER_USERNAME`: your login name, e.g. `ag`.
   - `FOUNDER_PASSWORD`: a strong password, 12+ characters.

   `SECRET_KEY` and `CRON_SECRET` are generated for you.
3. Click **Apply**. The first build takes about 3–5 minutes. It:
   - installs everything
   - creates the database tables and the shared login-lockout table
   - locks Supabase's API out of every table
   - runs Django's production safety checks
   - creates your founder login

   If any step fails, nothing goes live.
4. In the service → **Settings → Auto-Deploy**, choose **After CI checks pass**, so a change whose tests fail never goes live.
5. Open `https://studio-ops.onrender.com` (or whatever name Render shows) and sign in.

*Forgot the founder password?* In Render → Environment, change `FOUNDER_PASSWORD`, add `FOUNDER_RESET_PASSWORD` = `1`, then **Manual Deploy**. Remove `FOUNDER_RESET_PASSWORD` afterwards.

## 3. Turn on the safety nets in GitHub (5 min)

In the repo → **Settings**:
- **Secrets and variables → Actions → New repository secret:**
  - `SUPABASE_DB_URL` = the same Session pooler URI as `DATABASE_URL`.
  - `BACKUP_PASSPHRASE` = a long passphrase. **Also save it in your password manager, because without it the backups can't be opened.**
- **Actions → Backup → Run workflow** once to test it. After that it runs every Sunday at 02:00 IST and keeps each backup for 90 days (Actions → Backup → a run → *Artifacts*).
- **Branches → Add rule for `main`:** require a pull request and the **CI / test** check.

## 4. Two scheduled calls on cron-job.org (5 min)

Sign up at **cron-job.org** and create two cronjobs:

| Title | URL | Schedule | Extra settings |
|---|---|---|---|
| Keep awake | `https://<your-app>.onrender.com/health/` | Every **10 minutes** | none. `/health/` doesn't touch the database. |
| Daily job | `https://<your-app>.onrender.com/cron/daily/` | Every day at **03:00** (Asia/Kolkata) | **Advanced → Headers:** add `Authorization` = `Bearer <CRON_SECRET>`. Copy the value from Render → Environment → `CRON_SECRET`. |

The daily job marks past-due invoices **Overdue**, clears expired sign-ins and, by touching the database, stops Supabase pausing the project during quiet weeks such as Diwali. Without the right header it returns 401 and does nothing.

## Custom domain: redwed.in

The app answers on **https://redwed.in** and **https://www.redwed.in** (allowed in `config/settings.py`; a different domain can be set with the `STUDIO_DOMAINS` environment variable, comma-separated).

1. Render → the service → **Settings → Custom Domains**: add `redwed.in` and `www.redwed.in`. Render shows the DNS records and issues the HTTPS certificate once DNS points at it.
2. GoDaddy → DNS: an **A** record for `@` with Render's IP, and a **CNAME** for `www` pointing at `<your-app>.onrender.com`, as Render shows.
3. On cron-job.org, the two calls in step 4 can use `https://redwed.in/health/` and `https://redwed.in/cron/daily/`. The onrender.com address keeps working too.

## 5. First-time setup inside the app (15 min)

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
  - Glance at Supabase → **Database** size (limit 500 MB) and cron-job.org's history (both jobs green).

## Restoring a backup

```bash
gpg -d studio-ops-2026-11-08.dump.gpg > db.dump          # asks for BACKUP_PASSPHRASE
pg_restore --clean --if-exists --no-owner -d "<Session pooler URI>" db.dump
```

## Updating the app later

Changes go through GitHub (see CONTRIBUTING.md): a branch, then a pull request, then green tests, then merge. Render redeploys after the checks pass, and database changes are applied during the build.

## When you outgrow free

| Symptom | Upgrade |
|---|---|
| The rare 1-minute wake-up bothers people, pages feel slow at busy times, or you want production-grade hosting | Render **Starter** (about $7/month: never sleeps, more memory) |
| Database close to 500 MB, or you want managed daily backups | Supabase **Pro** (from $25/month) |

Nothing in the code changes for either upgrade.
