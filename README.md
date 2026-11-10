# Studio Ops · Red Wed Films

Private web app for running the editing studio: projects, planner, deadlines, daily logs, QC, delivery, hard drives, invoices, payroll, extra work, and Trailblazer of the Month. Founders, team leads, editors, QC and the drive desk each log in and see only what their role allows.

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
| **Founder** (admin) | Everything: dashboard, projects, planner, deadlines, delivered, reports, invoices, payroll, team and attendance, value vs salary, extra work, Trailblazer of the Month, agencies, settings, audit log, backup, back office. Can also use the drive desk, pod review and QC screens. |
| **Team lead** | Their pod's jobs and reviews (only editors in their own pod), plus their own Today, month, jobs, extra work, payslips and the leaderboard. |
| **Intern** | Their own Today, calendar, jobs, payslips and the leaderboard. No paid extra work. Can be assigned films and planned like editors. |
| **Editor** | Their own Today, month calendar, jobs (send for review, close with Drive links), extra work, payslips and the leaderboard. |
| **QC** | The QC dashboard: the queue with checklist, pass/fail and history, a status board of every film that reached QC (waiting, back for re-check, sent back, passed, delivered), plus the leaderboard. |
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
- **Usual hours:** the average hours of your own delivered films for each deliverable. Until a type has history, starting estimates are used (e.g. Trailer 20 h).
- **Capacity:** usual hours minus hours already logged on each open film, compared with that week's free hours. Leave, holidays and half days are taken out.
- **Leave:** each full leave counts 1 and each half day counts 0.5 (a studio half-day holiday isn't counted). Free leaves per month come from Settings (default 2). Anything beyond is deducted at salary ÷ days in the month, or ÷ 26 if you choose that in Settings.
- **Extra work:** hourly jobs pay hours × rate as soon as they're logged. Fixed jobs count in the month they're closed. Entries that overlap office time are flagged for a look but still counted.
- **Value of work:** each INR film's invoice is shared by the hours each editor spent on it. A film's hourly value = invoice ÷ max(actual hours, usual hours).
- **Client report dates:** your deadline plus N working days (skipping holidays), unless you override the stage or date for that film. Overrides are remembered.
- **Invoices:** numbered by date like your current ones (DDMMYYYY, `-2` for a second invoice the same day). A Sent invoice past its due date becomes Overdue automatically.
- **Planner, automatically:** creating or assigning a film with an editor and deadline plans enough working days for it (from its usual hours) on that editor's empty days before the deadline. Nothing already planned is overwritten, and a warning shows if it doesn't fit. Reassigning moves the future plan, and approving a delay extends it. **⤓** copies a planner cell to the next N working days, skipping weekly offs, holidays and leave.
- **Statuses:** Not started → In progress → TL review (fail → **TL revisions**) → In QC (fail → **QC revisions**) → QC passed → **Sent to agency** → Delivered. Sending to the agency counts as our delivery: on time or late is judged on that date, the film stops showing as overdue, and it counts in this month's delivered films and revenue. **Client revision** reopens a delivered film.
- **Client revisions:** founders reopen a delivered film with the client's changes, a revision due date and an editor. It goes back through review and QC and is closed again. The first delivery date and whether it was on time never change, so revisions never make a film "late". Each round is kept on the job.
- **Delivery tag:** on time / late is worked out from the first delivery vs the deadline at that time. Founders can override it on the Delivered page or the job: *On time*, *Delivered late*, or *Late due to client approval* (not counted against the studio), with a note.
- **Agency nickname:** up to 3 letters, unique (e.g. BRC). It's added to the end of every project name: "Riya & Arjun – Teaser – BRC".
- **Hours:** office hours are 9 a day including a 1-hour break; **productive hours** = in/out time minus the break (only on days longer than 5 h), so 9 h in office = 8 h productive. Productive hours drive task splitting, capacity, value and hourly contract pay. Both are adjustable in Settings.
- **Projects without reloads:** every edit in the list (status, editor, deadlines, invoice) and Assign in *Needs an editor* saves in the background; only that row is redrawn, with a confirmation and Undo. Filters, status chips and search (as you type) update the list in place and keep the link bookmarkable.
- **Projects by agency → couple → films (default view):** each agency is a folder showing its couples, open and overdue films, films that need an editor and value; open it to see its couples, each with films delivered (e.g. 2/4), next deadline, editors and value. Open a couple to see its films (loaded only then) and edit them in place. Couple actions: assign every open film to one editor (fills the Planner), set the status of all, move deadlines by N days, add another film (agency rate by default), and edit the couple (rename for every film, wedding date, notes). Films already in the portal were grouped automatically (same agency + same couple name, however it's spelt: "Amit & Tanya" = "amit and tanya"); look-alike names get a *Same couple?* hint with Merge. What's open is remembered in the browser; *List* switches back to one row per film. Filters and search work in both views.
- **Agency column** on Projects shows the agency's nickname (hover for the full name).
- **Edit from the list:** on Projects, status is a dropdown that saves as soon as you pick (Delivered also records the delivery date); editor's deadline, agency's deadline and invoice amount are editable in place and save when you leave the field or press Enter (Esc undoes). The editor's deadline can't go past the agency's, a new editor's deadline updates the Planner, filters are kept, and every change is in the audit log.
- **Projects filters:** quick chips (Open, Past deadline, Any revisions, each status) plus search (couple, agency, nickname, drive, brief), agency, editor (or unassigned), film type, deadline window (overdue / today / this week / 14 days / this month / editor's date after agency's / none), footage source, extra work / had a delay / had client revisions, and sort. Filters live in the link, so a filtered view can be bookmarked.
- **Advance leave:** editors apply under **My leave** (from tomorrow onward; half day for a single date; up to 31 days). Nothing reaches the calendar until a founder approves under **Leave requests** (badge in the menu, alert on the dashboard), which shows the editor's deadlines, planned work and who else is off on those dates. Approving marks each working day as Leave or Half day (weekly offs and studio holidays skipped) and clears planned work; declining or cancelling an approved leave keeps it on record. Leave on a future day can't be picked on the Today page without approval.
- **QC status board:** below the queue, every film that reached QC in the last 4 months with its state (Waiting for QC, Back for re-check, Sent back, Passed, Delivered), filters including "Failed at least once", QC rounds, the latest result with reviewer and issues, and a first-time-pass mark.
- **Day colours:** Leave red, Half day amber (split cell), Holiday blue, WFH green, Downtime violet, Weekly off grey stripes, each with a coloured left edge so they read apart in light and dark mode. Delivery days keep their red underline on top.
- **Revenue estimated vs delivered:** the dashboard shows the value of every film logged this month the moment it is added (INR, with other currencies listed alongside), next to revenue delivered, the share of the estimate already delivered and the INR value still sitting in open films.
- **Contract editors have no login.** Adding a contract person skips the login ID and password; they appear in the Planner, on films and in the editor dropdowns, and can't sign in (even an older contract login is refused). Founders log their days or hours on their Contract editors page (hourly: start and end time; daily: full or half day), set per-film fees and record payments there.
- **Projects list editing:** status, editor (dropdown, re-plans the Planner), editor's and agency's deadlines and the invoice amount are all changed in place. Columns can be dragged to a new position and resized from their edge (double-click the edge to fit); the layout is remembered in that browser, with a Reset columns button.
- **Date picker:** one calendar for every date and month field: Monday-first weeks, today ringed, Sundays marked, out-of-range days greyed out, quick picks (Today, Tomorrow, +1 week, Clear), full keyboard support, and a bottom sheet on phones. Dates can also be typed: `12 nov`, `12/11`, `12/11/26`, `tomorrow`, `+3`, `+2w`, `fri`.
- **Planner:** two editable views, **4 weeks** (a row per day) and **Month** (a row per editor). Click a cell to open a picker with search (that editor's own films listed first), main work or *alongside*, Copy to next N working days, and Clear. Drag a film to another cell or person to move it (hold Alt/Option to copy; a busy day takes it alongside). Every change saves in the background without reloading, with a confirmation and Undo. Arrow keys move between cells, Enter opens, Delete clears. Each film keeps one colour everywhere. Leave days are locked.
- **Two things at once:** each planner day has a main slot and an **alongside** slot (a second film, or *Revisions* on a delivered film). Today and My month show both. Auto-planning uses empty days first, then goes alongside other work if the days before the deadline are full. Clearing the main slot promotes the second one.
- **Two deadlines per film:** the **editor's deadline** (what editors see and work to) and the **agency's deadline** (founders only, never sent to any editor page; a test checks this). The editor's deadline can't be after the agency's. Client reports use the agency's deadline unless you override it, and delay requests show whether the new date still meets it.
- **New project:** one job per ticked film, each with its own editor's deadline, agency's deadline and invoice amount (blank = the shared value or the agency's usual rate). Footage can be on a hard drive or **Cloud / online** with the download link, which editors see on their job.
- **Delivery days** are marked in red on the deadlines calendar, each editor's month calendar and the planner (day count plus a "Due" tag in the editor's cell).
- **Delays:** an editor reports a delay from their job with a new date, a reason and a note. Founders approve (optionally with a different date) or decline. Approving moves the deadline, keeps the first deadline on record and updates the planner. The Delays page tracks pending requests, late films with no update, reasons, editors and on-time delivery against the first deadline.
- **Contract editors** are paid per film, hour, day or monthly retainer instead of through payroll. Earnings come from delivered films (with the per-film fee), logged hours or days worked. Founders record payments, and the balance due is tracked. Contract editors see their own statement under My payments.
- **Trailblazer of the Month:** the winner for month M is celebrated during month M+1. They get the gold look; everyone else gets a congrats banner.

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
- **Flows:** the day-log hour split, TL review → QC → close (including rejecting a non-Drive link), invoice totals and payment states, the payroll leave deduction, extra pay, holidays reducing capacity, Trailblazer of the Month, drive photos and the send-back guard, client-report overrides, and new projects.
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

## Performance

- **Database round-trips are budgeted.** Every main page runs a fixed, small number of queries (Team 17, Dashboard 25, Planner 12), independent of how many films or logged days exist. `ops/tests/test_performance.py` fails CI if a page goes over its budget or starts growing with the data.
- **Per-request memo** (`ops/perf.py`): studio settings, holidays, a month's day logs and extra work are read once per request. Any save clears it.
- **Shared caches** (database cache): every film's hours and the menu badges, dropped automatically on any related change (`ops/signals.py`) and on each deploy.
- **Sessions** are written at most every 5 minutes; database connections are kept for 10 minutes; indexes cover the list filters and date ranges.
- **Front end:** Geist is served by the app (no Google Fonts), static files are Brotli-compressed with far-future caching, Chrome/Edge preload a page when you hover a link (`/speculation-rules.json`), Projects shows 100 rows at a time with *Show more*, and date pickers in long tables are built as rows scroll into view.
- **Slow requests** (over 0.8 s) are logged in Render's logs with the page and query count.

## Security hardening (November 2026 review)

- **Two-step sign-in** (authenticator app, TOTP) for anyone, nudged for founders; 10 one-time recovery codes stored hashed; codes can't be replayed; 5 wrong codes restart sign-in, 10 per 15 min lock the second step. Founders can reset someone's two-step sign-in; `FOUNDER_RESET_2FA=1` recovers the founder (DEPLOY.md).
- **Back office login** (`/backoffice/login/`) redirects to the normal, throttled login, so it can't be used to guess passwords or skip two-step sign-in.
- **No state change from a link:** payroll save is POST-only (a GET used to reset the month's adjustments).
- **Links:** review links must be http(s) web addresses; "company Drive" links are checked on the exact host name; "go back to" redirects stay on this site.
- **Backup download** asks for the founder's password again (5 tries per 15 min) and leaves password hashes out of the file.
- **Sessions:** founders signed out after 2 h idle, everyone else after 12 h (`STAFF_IDLE_MINUTES`), so shared studio PCs don't stay signed in overnight.
- **Dependencies pinned** to exact versions; Dependabot proposes updates and CI runs pip-audit.

