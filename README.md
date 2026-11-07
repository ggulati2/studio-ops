# Studio Ops · Red Wed Films

Private web app for running the editing studio: projects, planner, deadlines, daily logs, QC, delivery, hard drives, invoices, payroll, extra work, and Employee of the Month. Founders, team leads, editors, QC and the drive desk each log in and see only what their role allows.

Deploying it for ₹0 (Render + Supabase): **[DEPLOY.md](DEPLOY.md)**.

---

## Architecture

```
 Phones / laptops ──HTTPS──▶  Render, Singapore (free web service)      Supabase Postgres, Singapore (free)
                              Django 5 · gunicorn · WhiteNoise   ──TLS──▶ all records incl. photos
                                                                           (session pooler, port 5432)
 cron-job.org ──▶ /health/ every 10 min (keeps Render awake, no DB hit)
              └─▶ /cron/daily/ 03:00 IST (overdue invoices, expired sessions, keeps Supabase active)
 GitHub Actions ──▶ tests on every push · encrypted pg_dump every Sunday
```

| Decision | Choice | Why |
|---|---|---|
| Framework | **Django 5** monolith, server-rendered HTML | Login, sessions, CSRF, permissions, database migrations and an admin back office come built in. One deployable unit, no separate frontend to host. Works on any phone browser. |
| Hosting | **Render free web service** (Starter, about $7/month, when it becomes critical) | ₹0, deploys from GitHub after CI passes, HTTPS included, no credit card. It sleeps after 15 min idle, so a free pinger keeps it awake. |
| Database | **Supabase Postgres** through the session pooler (SQLite locally) | Free 500 MB that doesn't expire. The pooler is IPv4, which Render needs. Supabase's auto-generated public API is locked out of every table at each deploy. |
| Region | **Both in Singapore** | Render has no India region. Keeping the database beside the app avoids a cross-region round-trip on every query. |
| Files / photos | **Shrunk in the browser, re-compressed on the server, stored in the database** | Free hosts have no permanent disk. Photos end up at roughly 150–300 KB. |
| Film files | **Links to the company Google Drive** | Project files and MP4s stay on Drive. The app stores links, never video. |
| PDFs | **Print-ready HTML pages** → browser "Save as PDF" | No PDF libraries to break. Invoices, status reports and payslips each have a Download / Print button. |
| Backups | **Weekly encrypted `pg_dump` in GitHub Actions** + in-app records download | Supabase Free has no backups. |

### Roles and permissions (RBAC)

Access is defined in one table, `ops/rbac.py`. Each view declares what it needs with `@requires("permission")`, and the menu is built from the same table, so a link is shown only when the server would also allow the page. Founders can see the live table in the app under **Settings → Who can see what**.

| Role | Can open |
|---|---|
| **Founder** (admin) | Everything: dashboard, projects, planner, deadlines, delivered, reports, invoices, payroll, team and attendance, value vs salary, extra work, Employee of the Month, agencies, settings, audit log, backup, back office. Can also use the drive desk, pod review and QC screens. |
| **Team lead** | Their pod's jobs and reviews (only editors in their own pod), plus their own Today, month, jobs, extra work, payslips and the leaderboard. |
| **Intern** | Their own Today, calendar, jobs, payslips and the leaderboard. No paid extra work. Can be assigned films and planned like editors. |
| **Editor** | Their own Today, month calendar, jobs (send for review, close with Drive links), extra work, payslips and the leaderboard. |
| **QC** | The QC queue with checklist, pass/fail and history, plus the leaderboard. |
| **Drive desk** | The drive register (receive with photos) and send-back records. Nothing else. |

Rules that hold on every request:
- **Private data is founders-only.** Invoices, payroll, salaries, attendance-vs-value, client rates, people management, the audit log and backups are marked `SENSITIVE`. The code refuses to start, and the tests fail, if any other role is given one of them.
- **Records are scoped by owner.** Editors can only open their own jobs, extra work and finalized payslips. Team leads can only review films from their own pod. Drive photos are visible only to the drive desk and founders.
- **Every page has a guard.** A test walks every URL and fails if a view has no `@requires(...)`.

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
- **Planner, automatically:** creating or assigning a film with an editor and deadline plans enough working days for it (from its usual hours) on that editor's empty days before the deadline. Nothing already planned is overwritten, and a warning shows if it doesn't fit. Reassigning moves the future plan, and approving a delay extends it. **⤓** copies a planner cell to the next N working days, skipping weekly offs, holidays and leave.
- **Two things at once:** each planner day has a main slot and an **alongside** slot (a second film, or *Revisions* on a delivered film). Today and My month show both. Auto-planning uses empty days first, then goes alongside other work if the days before the deadline are full. Clearing the main slot promotes the second one.
- **Two deadlines per film:** the **editor's deadline** (what editors see and work to) and the **agency's deadline** (founders only, never sent to any editor page; a test checks this). The editor's deadline can't be after the agency's. Client reports use the agency's deadline unless you override it, and delay requests show whether the new date still meets it.
- **New project:** one job per ticked film, each with its own editor's deadline, agency's deadline and invoice amount (blank = the shared value or the agency's usual rate). Footage can be on a hard drive or **Cloud / online** with the download link, which editors see on their job.
- **Delivery days** are marked in red on the deadlines calendar, each editor's month calendar and the planner (day count plus a "Due" tag in the editor's cell).
- **Delays:** an editor reports a delay from their job with a new date, a reason and a note. Founders approve (optionally with a different date) or decline. Approving moves the deadline, keeps the first deadline on record and updates the planner. The Delays page tracks pending requests, late films with no update, reasons, editors and on-time delivery against the first deadline.
- **Contract editors** are paid per film, hour, day or monthly retainer instead of through payroll. Earnings come from delivered films (with the per-film fee), logged hours or days worked. Founders record payments, and the balance due is tracked. Contract editors see their own statement under My payments.
- **Employee of the Month:** the winner for month M is celebrated during month M+1. They get the gold look; everyone else gets a congrats banner.

### Code map

```
config/settings.py     environment-driven settings (security on when DEBUG is off)
ops/rbac.py            roles → permissions table, @requires decorator, pod scope
ops/middleware.py      security headers (CSP), forced password change, founder idle sign-out, audit of every change
ops/audit.py           audit log writer + sign-in/out signals
ops/models.py          data model
ops/services.py        business rules (hours, leave, payroll, capacity, value, invoice numbers, images)
ops/views/             admin.py · team.py · money.py · drives.py · common.py
ops/tests/             test_access.py · test_flows.py · test_security.py
templates/             base + per-area pages; templates/print/ = invoice, reports, payslip
static/                app.css (design system) · app.js (no inline scripts anywhere) · icons.svg
ops/management/commands/seed_demo.py      demo data (empty databases only)
ops/management/commands/ensure_founder.py founder login from env vars at deploy
ops/management/commands/secure_supabase.py locks Supabase's Data API out of all tables
render.yaml · build.sh                    Render service and deploy steps: migrate, cache table, lockdown, checks, founder
.github/workflows/ci.yml                  tests on Postgres + deploy checks + dependency audit on every push
.github/workflows/backup.yml              weekly encrypted database backup
```

### Security

| Area | What's in place |
|---|---|
| Accounts | Invite-only, with no sign-up page. Passwords are hashed with scrypt and must be 8+ characters, not common, not only numbers and not close to the name. A password a founder sets must be replaced by the person at their next sign-in. |
| Sign-in | A login name is locked for 15 min after 8 wrong passwords, and an IP after 30. Counters are stored in the database so every server process shares them. Every success, failure and lockout is logged. |
| Sessions | HttpOnly, SameSite=Lax and Secure cookies. Team sessions last 7 days (sliding). Founders are signed out after 2 idle hours (`ADMIN_IDLE_MINUTES`). |
| Authorisation | RBAC table plus per-record ownership checks (see above). The back office (`/backoffice/`) is open to founder (superuser) accounts only, and role changes keep that in sync. The last active founder can't be demoted or deactivated, and founders can't remove their own access. |
| Browser | HTTPS-only with HSTS (1 year). Content-Security-Policy with `script-src 'self'` (the templates contain no inline JavaScript), `frame-ancestors 'none'`, Permissions-Policy, nosniff, a same-origin referrer policy, and `no-store` caching on signed-in pages. CSRF protection on every form. |
| Uploads | Photos are decoded with Pillow to check they're real (JPEG/PNG/WebP/HEIC), limited to 10 MB / 40 MP / 6 per upload, then re-encoded, which strips any embedded payload. They're stored in the database and served only to permitted roles. |
| Audit log | Append-only: sign-ins, lockouts, blocked pages, people and role changes, backups, and every saved change (who, screen, record, non-secret values, IP). Founders read it at **Settings → Audit log**. Nobody can edit or delete entries, even in the back office. |
| Database | TLS-only connection. Supabase's REST/GraphQL Data API is denied on every table (row level security on, `anon`/`authenticated` privileges revoked), re-applied on every deploy. Weekly dumps are AES-256 encrypted before they leave the runner. |
| Supply chain | Dependabot (weekly) and `pip-audit` in CI. `manage.py check --deploy` must pass before every deploy. |

How to report a problem: see [SECURITY.md](SECURITY.md).

### Testing

```bash
DEBUG=1 python manage.py test ops
```

41 tests in four groups:
- **Access:** every role opens its own pages and gets 403 on the rest, the menu matches permissions, and every URL has a guard.
- **Flows:** the day-log hour split, TL review → QC → close (including rejecting a non-Drive link), invoice totals and payment states, the payroll leave deduction, extra pay, holidays reducing capacity, Employee of the Month, drive photos and the send-back guard, client-report overrides, and new projects.
- **Hosting:** Supabase pooler settings, the Render blueprint and build steps, trusted client IPs behind Cloudflare, the cron secret, the backup file, and the Data API lockdown (on Postgres in CI).
- **Security:** headers and CSP, no inline scripts, lockout, forced password change, weak passwords, founder idle timeout, role-to-back-office sync, the last-founder guard, fake and oversized uploads, photo permissions, and audit entries.

GitHub Actions runs the same suite on Postgres with production settings on every push and pull request.

### Working on the code (changes and features)

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: one branch per change → pull request → CI must be green → merge to `main` → Render deploys automatically.

### What it doesn't do (yet)

- **Sending:** it doesn't send email or WhatsApp itself. Every report, invoice and payslip opens as a PDF page you download or share from your phone. This keeps you in control of what gets sent.
- **Drive link check:** it only confirms a link is a Google Drive link. Proving it sits in the *company* Drive needs the Google Drive API, which is a possible next step.

### Running it locally

```bash
pip install -r requirements.txt
export DEBUG=1
python manage.py migrate
python manage.py seed_demo          # optional demo data; logins: ag / nikhil / pratik / qc / drives, password demo-pass-123
python manage.py runserver
```
