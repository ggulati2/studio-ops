# Deploy Studio Ops for ₹0

About 30–40 minutes, once. No credit card needed. You'll set up three free accounts:

| Service | What it does | Free limit that matters |
|---|---|---|
| **GitHub** | Holds the code (private repo) | Free private repos |
| **Neon** | The database | 1 GB storage per project, 100 compute-hours/month, sleeps after 5 min idle (wakes in ~1 s). Data isn't deleted. |
| **Render** | Runs the app at `https://<name>.onrender.com` | 750 free hours/month (enough for one app running 24/7). Sleeps after 15 min with no visits and takes about 1 minute to wake. No permanent disk, which is why data lives on Neon. |
| **cron-job.org** (optional) | Visits `/health/` every 10 min so the app never sleeps | Free |

> Free tiers change. These limits were checked in November 2026. If either service changes its free plan, the app moves to any host that runs Python plus any Postgres database: just change `DATABASE_URL`.

---

## 1. Put the code on GitHub (5 min)

1. Sign up at github.com, then **New repository** → name `studio-ops` → choose **Private** → Create.
2. Click **uploading an existing file** and drag in **everything inside** the `studioops` folder (not the folder itself), including `render.yaml` and `build.sh`.
3. Click **Commit changes**.

## 2. Create the database on Neon (5 min)

1. Sign up at neon.com (you can sign in with GitHub).
2. Create a project:
   - Name: `studio-ops`
   - Postgres: the latest version
   - Region: **AWS Asia Pacific (Singapore)**, closest to Mumbai and Delhi
3. On the project dashboard, click **Connect**, turn on **Connection pooling**, and copy the connection string. It looks like:
   `postgresql://user:password@ep-xxxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`
4. Optional: under **Settings → Compute**, set the size to **0.25 CU** so the free hours last the whole month.

## 3. Deploy on Render (10 min)

1. Sign up at render.com **with GitHub** and allow access to the `studio-ops` repo.
2. Click **New → Blueprint** and pick the repo. Render reads `render.yaml` and asks for three values:
   - `DATABASE_URL`: paste the Neon string.
   - `FOUNDER_USERNAME`: your login name, e.g. `ag`.
   - `FOUNDER_PASSWORD`: a strong password, at least 8 characters.
3. Click **Apply**. The first build takes about 3–5 minutes. It installs everything, sets up the database tables and creates your founder login.
4. Open `https://studio-ops.onrender.com`, or whatever name Render shows, and sign in.

*Forgot the founder password?* In Render → Environment, change `FOUNDER_PASSWORD`, add `FOUNDER_RESET_PASSWORD` = `1`, then redeploy. Remove `FOUNDER_RESET_PASSWORD` afterwards.

## 4. Keep it awake (3 min, recommended)

Without this, the first person each morning waits about a minute.

1. Sign up at cron-job.org and choose **Create cronjob**.
2. URL: `https://<your-app>.onrender.com/health/`, every **10 minutes**.
3. Save.

`/health/` doesn't touch the database, so this doesn't use your Neon hours.

## 5. First-time setup inside the app (15 min)

1. **Settings:**
   - Studio details, PAN, bank account, IFSC, bank name, invoice email and GST %.
   - Leave policy (2 free leaves, how a day's pay is worked out) and shift start time.
   - **Studio holidays** for the year.
2. **Agencies:** add each client once, with billing address, invoice emails, currency, payment terms and **usual rates** (e.g. `Trailer = 18000`).
3. **Team & attendance → Add a person:** add each person with their role (Team lead, Editor, QC, Drive desk), pod and salary. Set a first password for each.
4. **Pods:** in **Back office → Pods**, create Pod 1/2/3 and set each lead.
5. Send each person the link, their login name and password. Ask them to use **Change password** on first login.

## 6. Every week

- **Settings → Download full backup:** keep the JSON file in your Google Drive. To restore: `python manage.py loaddata backup.json`.

## Updating the app later

Change files in the GitHub repo (or upload new versions). Render redeploys automatically and database changes are applied during the build.

## When you outgrow free

| Symptom | Upgrade |
|---|---|
| The ~1-minute wake-up bothers people and you don't want the pinger | Render **Starter** (paid, never sleeps) |
| Neon storage or compute is close to the limit (shown on the Neon dashboard) | Neon's paid plan |

Nothing in the code changes for either upgrade.

## Alternative host: PythonAnywhere (also free)

PythonAnywhere's free plan runs one always-on Django app with SQLite, so there's no wake-up delay. Two drawbacks:
- The app **expires if you don't click "Run until 1 month from today"** in their dashboard each month.
- Free accounts get community support only.

To use it, leave `DATABASE_URL` unset (the app falls back to SQLite), set `SECRET_KEY`, `ALLOWED_HOSTS=<you>.pythonanywhere.com` and `FOUNDER_*` in the WSGI file, then run the same `build.sh` steps in their console.
