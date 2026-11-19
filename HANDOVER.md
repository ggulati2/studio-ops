# Handover · Studio Ops (Red Wed Films)

Read this first when you start working on Studio Ops from a new Claude account or a new session. It covers where everything runs, how changes are made, how the owner likes to work, and the open ideas. The README documents every feature and business rule. DEPLOY.md covers hosting from scratch, and CONTRIBUTING.md covers the code rules.

*Written 20 Nov 2026 at commit `24991f5` (71 commits; the latest is AutoCheck per event).*

---

## 1. The business in one paragraph

**Red Wed Films** (RWF, "elevating love stories", brand colour red) is a wedding-film editing studio: an outsourced editing service for wedding photography agencies, plus some events and ads. The legal entity is **Rigger Films LLP**, and invoices go out under that name with the Rigger Films logo.

- **Owner:** AG, also called GG (Gourav Gulati). He's a founder and uses Studio Ops as admin.
- **Team:**
  - 2 founders
  - three pods, each with a team lead and one editor: Nikhil + Pratik, Soham + Himanshu, Harsh + Shabnam
  - 1 freelance editor
  - 1 QC person (Teena)
  - a drive desk
- **How the work runs:** the team edits in Premiere Pro, and each editor owns one film at a time. Revisions come in alongside, taking anywhere from hours to days.
- **Main clients:** BadalRaja Company (Delhi) and Weddings By Shooting Star (US), at almost the same pricing. Also Elysia Films (US), Happy Frames (Mumbai) and Cheesecake Films (Mumbai). Smaller amounts come from Qurbat Films, Shutter Magik Films and Dream Diaries.
- **Reviews:** happen on **Frame.io**. The agency's own comments appear as "Rigger Films".
- **Staff policies:**
  - 2 free leaves a month; beyond that, a day's pay is deducted
  - 9-hour office day including a 1-hour break, which counts as 8 productive hours
  - 6-day week
  - **Trailblazer of the Month** award: a ₹1,000 Amazon voucher card
- **Workload and delivery rules:**
  - An editor with all 6 days assigned in the Planner is "Full"; fewer assigned days means they can take more.
  - A film is on time if it's sent to the agency by the agency's deadline. Client revisions never count as late.

## 2. Where everything lives

| What | Where | Notes |
|---|---|---|
| Code | GitHub **`ggulati2/studio-ops`** (private), branch `main` | The only thing a new Claude session needs access to. |
| App | **Render**, free web service `studio-ops`, Singapore, 512 MB | Auto-deploys from `main` **after CI passes**. Build is `build.sh` (migrate, cache table, Supabase lock-down, deploy checks, founder login). |
| Database | **Supabase** Postgres, Singapore, Session pooler (port 5432) | Free tier: 500 MB, pauses after a week idle (the daily cron prevents that). Photos are stored in the database too. |
| Domain | **redwed.in** / **www.redwed.in** (GoDaddy DNS → Render) | The onrender.com address works too. |
| Scheduled calls | **cron-job.org**: `/health/` every 10 min (keeps Render awake), `/cron/daily/` at 03:00 IST with `Authorization: Bearer <CRON_SECRET>` | |
| Backups | GitHub Actions **Backup** workflow: Sundays 02:00 IST, encrypted with `BACKUP_PASSPHRASE`, kept 90 days | Restore steps are in DEPLOY.md. |
| CI | GitHub Actions **CI** (`.github/workflows/ci.yml`): tests on Postgres, deploy checks, dependency audit | Render waits for it. |
| QC Helper | Chrome extension in **`qc_helper/`**, downloaded from the portal at `/qc/helper/` | Not in the Chrome Web Store. Installed with "Load unpacked". See §6. |

**Secrets are never in the repo.** They live in Render → Environment and GitHub → Settings → Secrets:
- `SECRET_KEY`, `DATABASE_URL`, `CRON_SECRET`, `FOUNDER_USERNAME`, `FOUNDER_PASSWORD`
- optional: `FOUNDER_RESET_PASSWORD`, `FOUNDER_RESET_2FA`, `STUDIO_DOMAINS`, `ALLOWED_HOSTS`, `TIME_ZONE`, `ADMIN_IDLE_MINUTES`, `STAFF_IDLE_MINUTES`, `HSTS_PRELOAD`, `SECURE_SSL_REDIRECT`
- GitHub only: `SUPABASE_DB_URL`, `BACKUP_PASSPHRASE`

Moving accounts changes none of this, because Render, Supabase and cron-job.org are tied to GitHub and the owner's logins, not to Claude.

## 3. Starting a new Claude session

1. In the new Claude account, connect GitHub and make sure it can reach `ggulati2/studio-ops` (same GitHub login, or add the account as a collaborator).
2. Start a session on the repo and paste something like:

   > This is Studio Ops, the web app for my editing studio Red Wed Films. Read HANDOVER.md, README.md and CONTRIBUTING.md first. Work the way HANDOVER.md §4 describes: implement, test, commit and push to main, wait for CI (Render deploys after it). Today I want: …

3. To test anything on Frame.io (AutoCheck), the session needs **Claude in Chrome** (the user's own Chrome). The cloud sandbox can't reach Frame.io.

## 4. How changes are made (the working routine)

- **Routine:** implement → run the tests → commit → **push straight to `main`** → wait for CI to go green → Render deploys by itself. (CONTRIBUTING.md describes branches and PRs. In practice the owner has had changes pushed to `main` once tests pass.)
- **Git identity:** `Gourav Gulati <ggulati2@users.noreply.github.com>`.
- **Commit messages:** a plain-English first line about what changed for the studio (e.g. "QC: founders can send a film or event for QC on the editor's behalf"), an optional body, then the attribution trailer the session asks for (so far `Co-Authored-By: Claude …` plus a `Claude-Session:` link).
- **Tests:** `DEBUG=1 SECRET_KEY=x python manage.py test ops` (215 tests). `SECRET_KEY` must be set or settings refuse to load.
- **Local run:**
  ```bash
  export DEBUG=1 SECRET_KEY=x
  python manage.py migrate && python manage.py createcachetable
  python manage.py seed_demo          # only on an empty DB; logins ag / nikhil / pratik / qc / drives, password demo-pass-123
  ALLOWED_HOSTS=127.0.0.1,localhost python manage.py runserver 127.0.0.1:8765
  ```
  `seed_demo` refuses a non-empty database. Delete `db.sqlite3` (git-ignored) to start over. The demo QC person is "Ritika (QC)".
- **Guard rails the tests enforce:**
  - Every URL needs `@requires("perm")` from `ops/rbac.py` (a URL-guard test).
  - Per-page **query budgets** live in `ops/tests/test_performance.py`. Avoid N+1 queries; use `select_related` / `prefetch_related` / annotate.
  - Model changes need a committed migration (CI checks). The latest is `0028_autocheck_auto`.
  - Anything touching money, salaries, clients or people stays founder-only (`SENSITIVE` in `rbac.py`).
- **Front-end rules:**
  - **CSP: no inline JS.** Behaviour goes in `static/*.js` and is driven by `data-` attributes.
  - The owner wants everything **fluid (no page reloads)**:
    - `data-live` forms post JSON.
    - `data-live-page` forms inside a `[data-live-region]` are sent with an `X-Live-Page` header; the server redirects back to the page and the regions are swapped in place.
    - `window.StudioToast(text, {level})` shows a toast.
    - `StudioLive.reload` exists only on filter pages.
- **Hosting limits to respect:** Render free has 512 MB, so watch memory (an earlier fix: `ef01e7d`). Keep Supabase egress low (`cca7788`).

## 5. How the owner likes to work

- **Comprehensive, expert-level features.** Don't build the minimal version.
- **Don't ask about obviously useful next steps.** Do them.
- **"Suggest" / "don't implement" / "tell me the plan first" means give the plan and wait.** "build" / "implement" / "go" means do it, commit and push.
- **He reviews every email before it's sent.** Never send one automatically.
- **Plain language** in the UI, QC comments and replies: short sentences, no jargon.
- **Fluid UI**, no reloads.
- **Money is hidden on the dashboard** behind an icon-only eye. Amounts are left empty, not blurred.
- **Explain calculations with a worked example** when he asks how something is calculated (e.g. value of work vs cost).

## 6. AutoCheck & the Red Wed QC Helper (the newest and most delicate part)

**What it does.** It checks a new version of a film on Frame.io against the QC comments on the previous version (fixed / not fixed / "Teena needs to check"). It also looks for new issues:
- crushed blacks, blown highlights, black or flash frames
- jump cuts, colour jumps, camera shake
- audio near 0 dB, uneven loudness, pops, sudden silence
- length outside the rules

It then posts **public** comments on the new version starting with `[AutoCheck]`. It costs nothing: no AI service, and everything runs in the QC person's Chrome.

**Flow (since `24991f5`):**
1. Every link sent (an editor's event or whole film, a founder's "Send a film for QC", or a new link set in a review) is stored as a **`CutLink`** with a round number: V1, V2, V3… (`services.note_cut_link`).
2. If it's a Frame.io link, an **`AutoCheckRun`** is queued at once (`auto=True`):
   - **Round 1:** only the hygiene pass, on V1.
   - **Later rounds:** the previous version vs the new one.
   - An older queued run for the same event is replaced.
   - The same link sent again creates nothing new, and the QC page flags it.
3. A QC-role user's Chrome with the helper and **any Studio Ops page open** polls `POST /qc/autocheck/claim/` every 60 s (`static/autocheck.js`):
   - It runs one check at a time per person.
   - A check silent for 30 min is re-queued, twice at most.
   - Founders never claim automatically, but can click **Run now here**.
4. The extension opens the run in **its own unfocused window** (`runner.html`). The window comes forward for a few seconds while the Frame.io tabs load.
5. Progress, the result and the report go back to the portal tab (`autocheck:*` messages via `bridge.js`). Results are kept in `chrome.storage` until the portal confirms it saved them, so a closed tab doesn't lose them.
6. The QC page shows an **AutoCheck box inside each event** (and the whole-film review): both versions, status, the result, the report link, Change links and Run now / Run again. Editors never see AutoCheck in the portal.
7. Founders set rules (length, sensitivity, which checks run) per agency and film type under **QC → AutoCheck rules**.

**Code:**

| Area | Files |
|---|---|
| Models | `AutoCheckRule`, `AutoCheckRun`, `CutLink` in `ops/models.py` |
| Views | `ops/views/autocheck.py` (start, claim, update, report, rules, helper page and zip) |
| QC page assembly | `_autocheck_units` in `ops/views/team.py` |
| Templates | `team/_autocheck.html`, `_autocheck_helper.html`, `autocheck_report.html`, `autocheck_rules.html`, `qc_helper.html` |
| Portal JS | `static/autocheck.js` |
| Extension | `qc_helper/`: `manifest.json` (version **1.2.0**), `background.js`, `bridge.js`, `runner.js`, `analyse.js`, `fio.js` |

**Releasing a helper change:**
1. Bump `version` in `qc_helper/manifest.json`. The portal compares it and shows "update available".
2. Teena removes the old extension in `chrome://extensions`, downloads the zip from `/qc/helper/` and loads it unpacked.

**Frame.io facts the helper relies on.** Frame.io's page can change, so re-check these first if it breaks.

- **Share page** is `next.frame.io`, with stable `data-testid` attributes:
  - player: `overlay-play-button`, `play-button`, `timecode`, `time-display`
  - composer: `create-comment-comment-composer` (contenteditable), `create-comment-composer-container`, `composer-submit-button`
  - comments: `comment-cell-<uuid>`, `comment-prefix-timestamp`, `comment-author-name`, `comment-tab-container`
- **Media:**
  - HLS fMP4 on `sahls.frame.io/.../<rendition>/N.m4s` plus `init.mp4`, with 2-second segments numbered from 1.
  - Renditions: `h264_1080_best`, `h264_360`, …; audio is `aac_cha2`.
  - All renditions share one token path.
  - URLs only appear in `performance` entries after pressing play (`fioPrepare`).
  - The 180p proxy is often not loaded, so the quick pass plays the **360p** stream at 16× (`AC.quickPassHLS`).
  - The stream server sometimes stalls on a segment. `AC.get` adds a time limit and retries.
- **Reading frames:** MSE + canvas works and isn't tainted.
- **Posting a comment:**
  1. Seek, click the timecode, press Escape, focus the composer.
  2. Type through `InputEvent('beforeinput', {inputType: 'insertText'})`. `execCommand` doesn't update the editor.
  3. Avoid `#` in comment text, because it turns into a link.

**Calibration** (real V1/V2 pair, 19 Nov: `https://f.io/uVO4cwDv` → `https://f.io/5o99v9JU`, 36 min, 18 comments):
- **Verdicts matched a human look:**
  - font changed
  - exposure ~35% adjusted
  - blacks still crushed
  - speech section re-edited (+15 s)
  - trim done
  - shake at 8:54 still there
  - jump at 10:05 re-edited
- **Titles** are judged by the most-changed 8×6 region (`AC.blocks`, ≥12 = changed).
- **Shake threshold** is 0.75 × sensitivity (shots sampled at 320 px).
- **Loudness** flags a spread over 15 dB × sensitivity.
- **Audio glitches** too subtle to measure in V1 go to "needs to check".
- **No false jump, colour or flash flags** came up in 213 cuts.
- **Runtime** is about 15–30 min per film, depending on whether the runner window is visible.

**Known limits / ideas:**
- A version stacked on the same Frame.io link can't be compared yet. Reading the version stack would fix that.
- [AutoCheck] comments are public on Frame.io, so editors see them there.
- It only runs while a QC person's Chrome has Studio Ops open.

## 7. What's been built recently (newest first)

The details for each are in the README.

- **AutoCheck** (19 Nov): the helper extension, per-event boxes, automatic queue on send.
- **Value of work vs cost**:
  - based on days actually worked from daily logs; revisions included; part days counted in proportion; plan vs worked and overrun shown
  - month / FY / overall switch
  - cost = salary + extra pay
  - films shown on hover
- **Dashboard money** hidden behind an icon-only eye. FY revenue estimate and estimated profit (FY 2026-27 counted from 1 Nov 2026).
- **Event-wise QC** for long films (Haldi, Sangeet… sent one by one). The film's status follows its events (`whole_review` flag + `sync_film_status`).
- **QC:**
  - **My earnings**: close from QC, a founder-set fee per film
  - founders edit or delete QC entries, and send a film or event for QC on an editor's behalf
  - QC checklist and QC leaderboard removed
  - QC and Contract editors merged into one founders' menu item with tabs
- **Two editors on a film:** Editor 1 / Editor 2 with from–until dates (`EditorSpell`). Value, Planner and Projects follow.
- **Drives ↔ projects:** pick films in a drive's Contents; footage suggested when adding a film; editors see their drive and photos (view only).
- **Planner:** revisions can be planned for Client revision films. Team load comes from the Planner.
- **Start date is required** everywhere a film is added. Missing ones show as a chip on Projects and a dashboard KPI.
- **Extra work:** paid per film, no accepting needed. The `revision_charge` field holds a paid revision's charge.

## 8. Parked ideas (discussed; the owner said "don't implement" for now)

1. **Daily tasks as a PDF on WhatsApp every day.** Each person's planned and logged work for the day, as one PDF sent to the owner's WhatsApp each evening. Options were discussed (WhatsApp Cloud API needs a Meta business number and approved templates; free paths are more manual). Needs a decision on the sending route before building.
2. **Flow & efficiency analytics: "Time to Value" and "Time to Operationalisation".** How long from footage received → editing started → first cut → QC passed → sent to agency, per film, editor and agency, with bottlenecks shown. The data mostly exists already: start dates, review history, sent dates, `CutLink` rounds.
3. **Editor performance page / meter.** Films done, delivered on time vs late, QC first-time pass rate and revision rounds for each editor, possibly shown as a meter. Decide whether editors see their own page or only founders do.

## 9. Gotchas worth knowing

- `config/settings.py` raises an error without `SECRET_KEY`. With `DATABASE_URL` set, it adds `sslmode` (don't point it at SQLite).
- `seed_demo` needs an empty DB and the cache table (`createcachetable`).
- Django multi-line comments need `{% comment %}…{% endcomment %}`. A multi-line `{# #}` renders as text.
- Don't nest forms. The whole-film QC review card is itself a `<form>`, so the AutoCheck box sits in its own card above it.
- CSRF cookie is HttpOnly. JS that posts reads the token from `<meta name="ac-csrf">` (QC and admin pages) or the form's hidden input.
- The demo data has fixed users. The test helper logs in with `self.login("qc")`, etc.
