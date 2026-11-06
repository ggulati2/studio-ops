# Studio Ops · Red Wed Films

Private web app for running the editing studio: projects, planner, deadlines, daily logs, QC, delivery, hard drives, invoices, payroll, extra work, and Employee of the Month. Founders, team leads, editors, QC and the drive desk each log in and see only what their role allows.

Deploying it for free: **[DEPLOY.md](DEPLOY.md)**.

---

## Architecture

```
 Phones / laptops ──HTTPS──▶  Render (free web service)          Neon (free Postgres)
                              Django 5 · gunicorn · WhiteNoise ───▶ all data incl. photos
                              server-rendered pages
          cron-job.org ──▶ /health/ every 10 min (keeps it awake, no DB hit)
```

| Decision | Choice | Why |
|---|---|---|
| Framework | **Django 5** monolith, server-rendered HTML | Login, sessions, CSRF, permissions, database migrations and an admin back office come built in. One deployable unit, no separate frontend to host. Works on any phone browser. |
| Database | **PostgreSQL on Neon** (SQLite when running locally) | Neon's free tier doesn't expire. Render's free Postgres is deleted after 30 days, so it's not used. The same code runs on both via `DATABASE_URL`. |
| Hosting | **Render free web service** | Free, deploys from GitHub on every push, HTTPS included, no credit card. Sleeps after 15 min idle, so a free pinger keeps it awake. |
| Files / photos | **Compressed JPEGs stored in the database** | Free web hosts have no permanent disk. Photos are shrunk to ~100–300 KB, so 1 GB holds thousands. |
| Film files | **Links to the company Google Drive** | Project files and MP4s stay on Drive. The app stores links, never video. |
| PDFs | **Print-ready HTML pages** → browser "Save as PDF" | No PDF libraries or system packages to break on a free host. Invoices, status reports and payslips each have a Download / Print button. |
| Static files | **WhiteNoise** | Served compressed and cached by the app itself, with no CDN or bucket to set up. |

### Roles and what they can open (enforced on the server, not just hidden menus)

| Role | Sees |
|---|---|
| **Admin (founders)** | Everything: dashboard, projects, planner, deadlines, delivered, reports, invoices, payroll, team and attendance, value vs salary, extra work, Employee of the Month, agencies, settings, back office. |
| **Team lead** | Their pod's jobs and today's status, review queue (approve → QC or send back), plus their own Today, month, jobs, extra work, payslips, leaderboard. No invoices or salaries. |
| **Editor** | Their own Today, month calendar, jobs (send for review, close with Drive links), extra work, payslips, leaderboard. |
| **QC** | QC queue with checklist, timecoded comments, pass/fail, history; leaderboard. |
| **Drive desk** | Drive register (receive with photos), send-back records. Nothing else. |

All 90 page and permission checks and 40 workflow and calculation checks pass (see *Testing* below).

### How things flow

```
New project (one job per deliverable, agency's usual rate) ─▶ Needs an editor queue ─▶ assigned
Editor logs days ─▶ sends cut ─▶ TL review ─▶ QC (checklist) ─▶ pass ─▶ editor pastes Drive
project + MP4 links ─▶ Closed/Delivered ─▶ Generate invoice ─▶ payment status in the row
Drive: received (photos) ─▶ jobs closed ─▶ "Ready to send back" ─▶ send-back recorded
Month end: Payroll draft (salary − unpaid leave + extra work ± adjustment) ─▶ Finalize ─▶ payslips
```

### Business rules (all in `ops/services.py`)

- **Hours per job:** typed hours are kept. Blank ones share what's left of the in/out time equally. Shifts past midnight count correctly.
- **Usual hours:** the average hours of your own delivered films for each deliverable and tier. Until a type has history, starting estimates are used (e.g. Trailer 20 h at Tier 2; Tier 1 ×0.7, Tier 3 ×1.4).
- **Capacity:** usual hours minus hours already logged on each open film, compared with that week's free hours. Leave, holidays and half days are taken out.
- **Leave:** each full leave counts 1 and each half day counts 0.5 (a studio half-day holiday isn't counted). Free leaves per month come from Settings (default 2). Anything beyond is deducted at salary ÷ days in the month, or ÷ 26 if you choose that in Settings.
- **Extra work:** hourly jobs pay hours × rate as soon as they're logged. Fixed jobs count in the month they're closed. Entries that overlap office time are flagged for a look but still counted.
- **Value of work:** each INR film's invoice is shared by the hours each editor spent on it. A film's hourly value = invoice ÷ max(actual hours, usual hours).
- **Client report dates:** your deadline plus N working days (skipping holidays), unless you override the stage or date for that film. Overrides are remembered.
- **Invoices:** numbered by date like your current ones (DDMMYYYY, `-2` for a second invoice the same day). A Sent invoice past its due date becomes Overdue automatically.
- **Employee of the Month:** the winner for month M is celebrated during month M+1. They get the gold look; everyone else gets a congrats banner.

### Code map

```
config/settings.py     environment-driven settings (security on when DEBUG is off)
ops/models.py          data model
ops/services.py        business rules (hours, leave, payroll, capacity, value, invoice numbers, images)
ops/access.py          @roles(...) decorator – every view declares who may open it
ops/views/             admin.py · team.py · money.py · drives.py · common.py
templates/             base + per-area pages; templates/print/ = invoice, reports, payslip
ops/management/commands/seed_demo.py      demo data (empty databases only)
ops/management/commands/ensure_founder.py founder login from env vars at deploy
```

### Security

- Invite-only: there is no sign-up page, and only founders create logins.
- Passwords are hashed by Django and must be at least 8 characters.
- Login is locked for 15 minutes after 8 wrong attempts.
- In production: HTTPS only, secure cookies, HSTS, CSRF on every form, and no framing.
- Money, salaries and client rates are only reachable by the admin role, and every money URL returns 403 for anyone else.
- Photos are served only to logged-in users.

### Testing

```bash
DEBUG=1 python manage.py migrate && DEBUG=1 python manage.py seed_demo
```

The checks used during the build log in as every role and open every page. They confirm each blocked page returns 403, then run the full workflows: day log hour split, TL review → QC → close (including rejecting a non-Drive link), invoice totals and payment states, the payroll leave deduction, extra pay, holidays reducing capacity, Employee of the Month, the drive send-back guard, and client-report overrides.

### What it doesn't do (yet)

- **Sending:** it doesn't send email or WhatsApp itself. Every report, invoice and payslip opens as a PDF page you download or share from your phone. This keeps it free and keeps you in control of what gets sent.
- **Drive link check:** it only confirms a link is a Google Drive link. Proving it sits in the *company* Drive needs the Google Drive API, which is a possible next step.
- **Uploads:** one photo per upload field. Add more by editing the drive again.

### Running it locally

```bash
pip install -r requirements.txt
export DEBUG=1
python manage.py migrate
python manage.py seed_demo          # optional demo data; logins: ag / nikhil / pratik / qc / drives, password demo-pass-123
python manage.py runserver
```
