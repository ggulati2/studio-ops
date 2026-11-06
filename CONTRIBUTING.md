# Working on Studio Ops

## The flow for every change

```
main (always deployable, protected)
  └─ feature/invoice-gst-split     ← one branch per change
       └─ pull request → CI (tests on Postgres, deploy checks, dependency audit) → review → merge
                                                                                     └─ Render deploys
```

1. **Start from an issue.** Use the *Feature request* or *Bug* template in GitHub → Issues, so changes are tracked against what the studio asked for.
2. **Branch:** `feature/<short-name>` or `fix/<short-name>`.
3. **Run it locally:**
   ```bash
   pip install -r requirements.txt
   export DEBUG=1
   python manage.py migrate && python manage.py createcachetable
   python manage.py seed_demo      # logins: ag / nikhil / pratik / qc / drives — password demo-pass-123
   python manage.py runserver
   ```
4. **Test:** `DEBUG=1 python manage.py test ops`. Add a test for any new rule or page.
5. **Model change?** Run `python manage.py makemigrations` and commit the migration. CI fails if you forget.
6. **Open a pull request.** The template asks for the role/permission impact. Merge only when CI is green.

## Rules that keep it safe

- **Every new page needs `@requires("some.permission")`** from `ops/rbac.py`. A test fails otherwise.
- **New permissions** go in `PERMISSIONS` and `ROLE_PERMISSIONS`. Anything touching money, salaries, client details or people goes in `SENSITIVE` and stays founder-only.
- **Filter by owner** in views that show personal records (`editor=request.user`, `user=request.user`).
- **No inline JavaScript** (`onclick=`, `<script>…</script>`), because the CSP blocks it. Add behaviour in `static/app.js` with a `data-` attribute.
- **Never commit secrets.** Settings come from environment variables; `.env` is git-ignored.

## Recommended GitHub settings (one-time, by the repo owner)

- Settings → Branches → add a rule for `main`: *Require a pull request* and *Require status checks to pass* (select **CI / test**).
- Settings → Code security: turn on *Dependabot alerts* and *Secret scanning*.
