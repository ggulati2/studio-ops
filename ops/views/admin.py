"""Founder (admin) screens: dashboard, projects, planner, deadlines, delivered,
reports, team, extra work, EOTM, agencies, settings."""
from collections import defaultdict
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..audit import record
from ..rbac import ADMIN, requires
from ..forms import (AgencyForm, AssignForm, EOTMForm, HolidayForm, NewProjectForm, PersonForm, ProjectForm,
                     StudioSettingsForm)
from ..models import (Agency, AgencyRate, DayLog, EmployeeOfMonth, ExtraEntry, Holiday, Invoice, PlanEntry, Pod,
                      Profile, Project, StudioSettings)
from .. import services as S
from ..services import (add_months, attendance, available_days, benchmarks, celebrated_award, client_view,
                        editor_value, extra_hours, extra_pay, holidays_between, hours_remaining, leave_count,
                        month_end, month_start, parse_month, project_hours, store_image, team, trophy_counts,
                        week_start)

User = get_user_model()
__all__ = ["backup", "dashboard", "projects", "project_new", "project_edit", "project_assign", "planner", "deadlines",
           "delivered", "reports", "report_print", "team_view", "person_edit", "extra_admin", "eotm",
           "agencies", "agency_edit", "settings_view", "holiday_delete", "audit_log", "access_matrix"]


def _load(user):
    """Booked hours left vs available hours this week."""
    ws = week_start(timezone.localdate())
    avail = available_days(user, ws, ws + timedelta(days=6)) * float(StudioSettings.get().hours_per_day)
    return avail


@requires("dashboard.view")
def dashboard(request):
    today = timezone.localdate()
    month = month_start(today)
    open_jobs = Project.objects.exclude(status__in=(Project.CLOSED, Project.ON_HOLD)).select_related("agency", "editor__profile")
    bench = benchmarks()
    rows = []
    for u in team():
        mine = [p for p in open_jobs if p.editor_id == u.id]
        left = sum(hours_remaining(p, bench) for p in mine)
        avail = _load(u)
        util = round(left / avail * 100) if avail else None
        rows.append({"u": u, "open": len(mine), "left": round(left, 1), "avail": avail, "util": util,
                     "status": "Overloaded" if util and util >= 100 else "Full" if util and util >= 80 else "Can take more"})
    yesterday = today - timedelta(days=1)
    logged = set(DayLog.objects.filter(date=yesterday).values_list("user_id", flat=True))
    missing = [u for u in team() if u.id not in logged and yesterday.weekday() in StudioSettings.get().weekdays
               and yesterday not in holidays_between(yesterday, yesterday)]
    delivered = Project.objects.filter(status=Project.CLOSED, closed_on__gte=month)
    on_time = [p for p in delivered if p.deadline]
    rev_by_agency = defaultdict(int)
    for p in delivered.select_related("agency"):
        if p.agency.currency == "INR":
            rev_by_agency[p.agency.name] += p.invoice_amount
    total_rev = sum(rev_by_agency.values()) or 0
    ctx = {
        "unassigned": open_jobs.filter(editor__isnull=True).count(),
        "overdue": [p for p in open_jobs if p.days_left is not None and p.days_left < 0],
        "due_soon": [p for p in open_jobs if p.days_left is not None and 0 <= p.days_left <= 3],
        "missing": missing,
        "delivered_count": delivered.count(),
        "on_time_pct": round(sum(1 for p in on_time if p.closed_on <= p.deadline) / len(on_time) * 100) if on_time else None,
        "revenue": total_rev,
        "agencies": sorted(({"name": k, "amount": v, "share": round(v / total_rev * 100) if total_rev else 0}
                            for k, v in rev_by_agency.items()), key=lambda x: -x["amount"]),
        "rows": rows,
        "outstanding": sum(i.balance for i in Invoice.objects.exclude(status__in=("paid", "draft")).filter(currency="INR")),
        "award": celebrated_award(),
    }
    return render(request, "founder/dashboard.html", ctx)


@requires("projects.manage")
def projects(request):
    qs = Project.objects.select_related("agency", "editor__profile", "drive")
    status = request.GET.get("status", "open")
    q = request.GET.get("q", "").strip()
    if status == "open":
        qs = qs.exclude(status__in=(Project.CLOSED, Project.ON_HOLD))
    elif status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(couple__icontains=q) | Q(agency__name__icontains=q) | Q(drive__code__icontains=q))
    queue = Project.objects.filter(editor__isnull=True).exclude(status__in=(Project.CLOSED, Project.ON_HOLD)).select_related("agency", "drive")
    bench = benchmarks()
    jobs = []
    for p in qs:
        h = project_hours(p)
        usual = S.usual_hours(p, bench)
        jobs.append({"p": p, "hours": h, "usual": usual, "over": round((h / usual - 1) * 100) if usual and h else None})
    return render(request, "founder/projects.html", {
        "jobs": jobs, "queue": queue, "status": status, "q": q, "statuses": Project.STATUSES,
        "assign_form": AssignForm(), "new_form": NewProjectForm()})


@requires("projects.manage")
def project_new(request):
    form = NewProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        c = form.cleaned_data
        editor = User.objects.filter(pk=c["editor"]).first() if c["editor"] else None
        rates = {r.deliverable: r.amount for r in c["agency"].rates.all()}
        with transaction.atomic():
            for d in c["deliverables"]:
                Project.objects.create(
                    couple=c["couple"], deliverable=d, agency=c["agency"], tier=c["tier"], editor=editor,
                    deadline=c["deadline"], drive=c["drive"], brief=c["brief"],
                    invoice_amount=c["invoice_amount"] if c["invoice_amount"] is not None else rates.get(d, 0),
                    is_extra=c["is_extra"] and bool(editor), extra_basis=c["extra_basis"] or "fixed",
                    extra_rate=c["extra_rate"] or 0)
        messages.success(request, f"Created {len(c['deliverables'])} job(s) for {c['couple']}.")
        return redirect("projects")
    return render(request, "form_page.html", {"form": form, "title": "New project", "back": "projects",
                                              "submit": "Create jobs"})


@requires("projects.manage")
def project_edit(request, pk):
    p = get_object_or_404(Project, pk=pk)
    form = ProjectForm(request.POST or None, instance=p)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if obj.status == Project.CLOSED and not obj.closed_on:
            obj.closed_on = timezone.localdate()
        obj.save()
        messages.success(request, "Job saved.")
        return redirect("projects")
    return render(request, "founder/project_edit.html", {"form": form, "p": p, "hours": project_hours(p),
                                                       "reviews": p.reviews.select_related("reviewer")})


@requires("projects.manage")
def project_assign(request, pk):
    p = get_object_or_404(Project, pk=pk)
    form = AssignForm(request.POST)
    if form.is_valid():
        p.editor = User.objects.get(pk=form.cleaned_data["editor"])
        p.save(update_fields=["editor"])
        messages.success(request, f"{p} assigned to {p.editor.profile.name}.")
    return redirect(request.POST.get("next") or "projects")


@requires("planner.manage")
def planner(request):
    start = request.GET.get("start")
    try:
        start = date.fromisoformat(start)
    except (TypeError, ValueError):
        start = week_start(timezone.localdate())
    days = [start + timedelta(days=i) for i in range(14)]
    people = list(team())
    if request.method == "POST":
        u = get_object_or_404(User, pk=request.POST["user"])
        d = date.fromisoformat(request.POST["date"])
        val = request.POST.get("value", "")
        PlanEntry.objects.filter(user=u, date=d).delete()
        if val.startswith("p:"):
            PlanEntry.objects.create(user=u, date=d, project_id=int(val[2:]))
        elif val.strip():
            PlanEntry.objects.create(user=u, date=d, label=val.strip()[:80])
        return redirect(f"{request.path}?start={start.isoformat()}")
    plan = {(e.user_id, e.date): e for e in PlanEntry.objects.filter(date__range=(days[0], days[-1])).select_related("project")}
    logs = {(l.user_id, l.date): l for l in DayLog.objects.filter(date__range=(days[0], days[-1]))}
    hols = holidays_between(days[0], days[-1])
    weekdays = StudioSettings.get().weekdays
    grid = []
    for d in days:
        cells = []
        for u in people:
            log = logs.get((u.id, d))
            kind = log.day_type if log and log.day_type in (DayLog.LEAVE, DayLog.HALF, DayLog.WFH, DayLog.HOLIDAY) else ""
            if not kind and d in hols:
                kind = "holiday" if hols[d].kind == "full" else "half" if hols[d].kind == "half" else ""
            if not kind and d.weekday() not in weekdays:
                kind = "off"
            cells.append({"u": u, "entry": plan.get((u.id, d)), "kind": kind})
        grid.append({"d": d, "cells": cells, "holiday": hols.get(d)})
    open_projects = Project.objects.exclude(status__in=(Project.CLOSED, Project.ON_HOLD)).select_related("agency")
    return render(request, "founder/planner.html", {
        "grid": grid, "people": people, "start": start, "prev": start - timedelta(days=14),
        "next": start + timedelta(days=14), "open_projects": open_projects,
        "tray": open_projects.filter(editor__isnull=True)})


@requires("deadlines.view")
def deadlines(request):
    month = parse_month(request.GET.get("m"))
    agency = request.GET.get("agency", "")
    editor = request.GET.get("editor", "")
    client = request.GET.get("view") == "client"
    buffer_days = StudioSettings.get().report_buffer_days
    qs = Project.objects.select_related("agency", "editor__profile")
    if agency:
        qs = qs.filter(agency_id=agency)
    if editor:
        qs = qs.filter(editor_id=editor)
    first = month_start(month)
    lead = (first.weekday() + 1) % 7  # Sunday-first grid
    grid_start = first - timedelta(days=lead)
    cells = []
    hols = holidays_between(grid_start, grid_start + timedelta(days=41))
    by_day = defaultdict(list)
    for p in qs:
        d = client_view(p, buffer_days)[1] if client else p.deadline
        if d:
            by_day[d].append(p)
    today = timezone.localdate()
    for i in range(42):
        d = grid_start + timedelta(days=i)
        if i >= 35 and d.month != month.month:
            break
        cells.append({"d": d, "in_month": d.month == month.month, "items": by_day.get(d, []),
                      "holiday": hols.get(d), "today": d == today})
    week = [p for p in qs if p.is_open and p.deadline and p.deadline <= today + timedelta(days=7)]
    week.sort(key=lambda p: p.deadline)
    sel = request.GET.get("day")
    try:
        sel = date.fromisoformat(sel)
    except (TypeError, ValueError):
        sel = today
    return render(request, "founder/deadlines.html", {
        "month": month, "prev": add_months(month, -1), "next": add_months(month, 1), "cells": cells,
        "agencies": Agency.objects.all(), "editors": team(), "agency": agency, "editor": editor, "client": client,
        "week": week, "sel": sel, "sel_items": by_day.get(sel, []),
        "unassigned_soon": qs.filter(editor__isnull=True, deadline__lte=today + timedelta(days=21)).exclude(status=Project.CLOSED).count()})


@requires("delivered.view")
def delivered(request):
    month = parse_month(request.GET.get("m"))
    qs = (Project.objects.filter(status=Project.CLOSED, closed_on__range=(month_start(month), month_end(month)))
          .select_related("agency", "editor__profile", "drive").order_by("-closed_on"))
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(couple__icontains=q) | Q(agency__name__icontains=q))
    rows = []
    for p in qs:
        try:
            inv = p.invoice_line.invoice
        except Exception:
            inv = None
        rows.append({"p": p, "qc_rounds": p.reviews.filter(kind="qc").count(), "invoice": inv})
    pending_close = Project.objects.filter(status=Project.QC_PASSED).select_related("editor__profile")
    from ..models import HardDrive
    drives = []
    for d in HardDrive.objects.exclude(status="sent").prefetch_related("projects"):
        jobs = list(d.projects.all())
        if jobs:
            open_ = [j for j in jobs if j.status != Project.CLOSED]
            drives.append({"d": d, "jobs": len(jobs), "open": open_, "safe": not open_})
    return render(request, "founder/delivered.html", {"rows": rows, "month": month, "prev": add_months(month, -1),
                                                    "next": add_months(month, 1), "q": q, "pending_close": pending_close,
                                                    "drives": drives})


@requires("reports.manage")
def reports(request):
    s = StudioSettings.get()
    agency_id = request.GET.get("agency")
    agency = Agency.objects.filter(pk=agency_id).first() if agency_id else None
    if request.method == "POST":
        for p in Project.objects.filter(pk__in=request.POST.getlist("pid")):
            p.client_stage = request.POST.get(f"stage_{p.pk}", "")
            raw = request.POST.get(f"date_{p.pk}", "")
            try:
                p.client_date = date.fromisoformat(raw) if raw else None
            except ValueError:
                p.client_date = None
            p.save(update_fields=["client_stage", "client_date"])
        messages.success(request, "Client view saved. It will be remembered for the next report.")
        return redirect(f"{request.path}?agency={agency_id}")
    rows = []
    if agency:
        for p in agency.projects.exclude(status=Project.CLOSED).select_related("editor__profile"):
            stage, when = client_view(p, s.report_buffer_days)
            rows.append({"p": p, "stage": stage, "when": when, "edited": bool(p.client_stage or p.client_date)})
    return render(request, "founder/reports.html", {"agencies": Agency.objects.filter(active=True), "agency": agency,
                                                  "rows": rows, "stages": Project.CLIENT_STAGES, "buffer": s.report_buffer_days})


@requires("reports.manage")
def report_print(request):
    s = StudioSettings.get()
    today = timezone.localdate()
    agency = Agency.objects.filter(pk=request.GET.get("agency")).first()
    note = request.GET.get("note", "")
    month = month_start(today)
    if agency:
        open_rows = []
        for p in agency.projects.exclude(status=Project.CLOSED).order_by("deadline"):
            stage, when = client_view(p, s.report_buffer_days)
            open_rows.append({"p": p, "stage": stage, "when": when})
        done = agency.projects.filter(status=Project.CLOSED, closed_on__gte=month).order_by("closed_on")
        return render(request, "print/agency_report.html", {
            "agency": agency, "open_rows": open_rows, "done": done, "note": note, "today": today,
            "final_checks": sum(1 for r in open_rows if r["stage"] == "Final checks")})
    groups = defaultdict(list)
    for p in Project.objects.exclude(status=Project.CLOSED).select_related("agency", "editor__profile", "drive").order_by("deadline"):
        groups[p.agency.name].append(p)
    return render(request, "print/team_report.html", {"groups": sorted(groups.items()), "note": note, "today": today})


@requires("attendance.view_all")
def team_view(request):
    month = parse_month(request.GET.get("m"))
    hpd = float(StudioSettings.get().hours_per_day)
    values = editor_value(month)
    rows = []
    for u in User.objects.filter(profile__isnull=False).exclude(profile__role=Profile.ADMIN).select_related("profile", "profile__pod").order_by("profile__pod__name", "first_name"):
        att = attendance(u, month)
        avail = available_days(u, month_start(month), min(month_end(month), timezone.localdate())) * hpd
        value = values.get(u.id, 0)
        sal = u.profile.monthly_salary
        rows.append({"u": u, "att": att, "avail": round(avail, 1),
                     "util": round(att["job_hours"] / avail * 100) if avail else None,
                     "value": value, "multiple": round(value / sal, 1) if sal else None})
    return render(request, "founder/team.html", {"rows": rows, "month": month, "prev": add_months(month, -1),
                                               "next": add_months(month, 1), "pods": Pod.objects.all()})


@requires("people.manage")
def person_edit(request, pk=None):
    user = get_object_or_404(User, pk=pk) if pk else None
    form = PersonForm(request.POST or None, instance=user)
    form.actor = request.user
    if request.method == "POST" and form.is_valid():
        c = form.cleaned_data
        created = user is None
        old_role = None if created else user.profile.role
        with transaction.atomic():
            if not user:
                user = User(username=c["username"])
            user.username, user.first_name, user.last_name, user.email = (c["username"], c["first_name"], c["last_name"],
                                                                          c["email"])
            user.is_active = c["active"]
            # Founders get the back office; nobody else does.
            user.is_staff = user.is_superuser = (c["role"] == Profile.ADMIN)
            if c["password"]:
                user.set_password(c["password"])
            user.save()
            p, _ = Profile.objects.get_or_create(user=user)
            p.role, p.pod, p.monthly_salary, p.joined_on, p.active = c["role"], c["pod"], c["monthly_salary"], c["joined_on"], c["active"]
            if c["password"] and user.pk != request.user.pk:
                p.must_change_password = True   # a password someone else knows must be replaced at first login
            p.save()
        record(request, "people.create" if created else "people.update", user.username,
               f"role {old_role or '-'} -> {c['role']}; active={c['active']}" + ("; password set" if c["password"] else ""))
        if c["password"] and user.pk == request.user.pk:
            from django.contrib.auth import update_session_auth_hash
            update_session_auth_hash(request, user)
        if c["password"] and user.pk != request.user.pk:
            # Show the login details once, on this response only (never stored or put in a cookie).
            resp = render(request, "founder/person_saved.html", {
                "person": user, "login_id": user.username, "password": c["password"], "created": created,
                "app_url": request.build_absolute_uri("/")})
            resp["Cache-Control"] = "no-store"
            return resp
        messages.success(request, f"Saved {user.profile.name}.")
        return redirect("team")
    return render(request, "founder/person_form.html", {"form": form, "person": user,
                                                        "title": f"Edit {user.profile.name}" if user else "Add a person"})


@requires("extra.manage")
def extra_admin(request):
    month = parse_month(request.GET.get("m"))
    a, b = month_start(month), month_end(month)
    entries = ExtraEntry.objects.filter(date__range=(a, b)).select_related("user__profile", "project")
    flagged = [e for e in entries if e.overlaps_office]
    rows = []
    for u in team():
        reg = sum(l.hours for l in DayLog.objects.filter(user=u, date__range=(a, b)))
        ex = extra_hours(u, month)
        weeks = max(((min(b, timezone.localdate()) - a).days + 1) / 7, 1)
        rows.append({"u": u, "jobs": Project.objects.filter(editor=u, is_extra=True, received_on__lte=b).exclude(
            closed_on__lt=a).count(), "extra": ex, "regular": round(reg, 1),
            "week": round((reg + ex) / weeks), "pay": extra_pay(u, month)})
    margins = []
    for p in Project.objects.filter(is_extra=True).select_related("editor__profile", "agency")[:30]:
        cost = (sum(e.pay for e in p.extra_entries.all()) if p.extra_basis == "hourly" else p.extra_rate)
        keep = p.invoice_amount - cost if p.agency.currency == "INR" else None
        margins.append({"p": p, "cost": cost, "keep": keep,
                        "pct": round(keep / p.invoice_amount * 100) if keep is not None and p.invoice_amount else None})
    return render(request, "founder/extra.html", {"month": month, "prev": add_months(month, -1), "next": add_months(month, 1),
                                                "rows": rows, "flagged": flagged, "margins": margins,
                                                "total_pay": sum(r["pay"] for r in rows),
                                                "total_hours": round(sum(r["extra"] for r in rows), 1)})


@requires("eotm.manage")
def eotm(request):
    form = EOTMForm(request.POST or None, request.FILES or None,
                    initial={"month": add_months(month_start(timezone.localdate()), -1),
                             "voucher_amount": StudioSettings.get().eotm_voucher_amount})
    if request.method == "POST" and form.is_valid():
        obj, created = EmployeeOfMonth.objects.update_or_create(
            month=form.cleaned_data["month"],
            defaults={k: form.cleaned_data[k] for k in ("user", "reason", "voucher_amount", "handed_over_on")})
        if form.cleaned_data.get("photo_upload"):
            obj.photo = store_image(form.cleaned_data["photo_upload"], max_side=800)
            obj.save(update_fields=["photo"])
        messages.success(request, f"{obj.user.profile.name} is Employee of the Month for {obj.month:%B %Y}.")
        return redirect("eotm")
    # Suggestions for last month: numbers only, the choice stays with the founders.
    last = add_months(month_start(timezone.localdate()), -1)
    sugg = []
    for u in team():
        closed = Project.objects.filter(editor=u, status=Project.CLOSED, closed_on__range=(last, month_end(last)))
        on_time = [p for p in closed if p.deadline and p.closed_on <= p.deadline]
        qc = Project.objects.filter(editor=u, reviews__kind="qc", reviews__created__date__range=(last, month_end(last))).distinct()
        first_pass = [p for p in qc if not p.reviews.filter(kind="qc", passed=False).exists()]
        sugg.append({"u": u, "films": closed.count(), "on_time": len(on_time),
                     "qc_first": round(len(first_pass) / len(qc) * 100) if qc else None,
                     "leaves": leave_count(u, last)})
    sugg.sort(key=lambda r: (-r["films"], -(r["qc_first"] or 0)))
    return render(request, "founder/eotm.html", {"form": form, "awards": EmployeeOfMonth.objects.select_related("user__profile"),
                                               "current": celebrated_award(), "tally": _tally(), "sugg": sugg})


def _tally():
    counts = trophy_counts()
    users = {u.id: u for u in User.objects.filter(id__in=counts).select_related("profile")}
    return sorted(((users[k], v) for k, v in counts.items()), key=lambda x: -x[1])


@requires("agencies.manage")
def agencies(request):
    rows = []
    year = date(timezone.localdate().year, 1, 1)
    for a in Agency.objects.prefetch_related("invoices__lines"):
        invs = list(a.invoices.all())
        rows.append({"a": a, "billed": sum(i.total for i in invs if i.issued_on >= year and i.status != "draft"),
                     "owed": sum(i.balance for i in invs if i.status not in ("paid", "draft"))})
    return render(request, "founder/agencies.html", {"rows": rows})


@requires("agencies.manage")
def agency_edit(request, pk=None):
    a = get_object_or_404(Agency, pk=pk) if pk else None
    form = AgencyForm(request.POST or None, instance=a)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            a = form.save()
            a.rates.all().delete()
            AgencyRate.objects.bulk_create([AgencyRate(agency=a, deliverable=k, amount=v)
                                            for k, v in form.cleaned_data["rates"].items()])
        messages.success(request, f"Saved {a.name}.")
        return redirect("agencies")
    return render(request, "form_page.html", {"form": form, "title": a.name if a else "New agency", "back": "agencies",
                                              "submit": "Save agency",
                                              "intro": "Saved once, used on every invoice, report and reminder."})


@requires("settings.manage")
def settings_view(request):
    s = StudioSettings.get()
    form = StudioSettingsForm(request.POST or None, instance=s, prefix="s")
    hform = HolidayForm(request.POST or None, prefix="h")
    if request.method == "POST":
        if "save_settings" in request.POST and form.is_valid():
            form.save()
            messages.success(request, "Settings saved.")
            return redirect("settings")
        if "add_holiday" in request.POST and hform.is_valid():
            h = hform.save()
            clashes = PlanEntry.objects.filter(date=h.date).count()
            messages.success(request, f"Added {h.name}." + (f" {clashes} planned job(s) that day need moving." if clashes else ""))
            return redirect("settings")
        if "copy_year" in request.POST:
            n = 0
            for h in Holiday.objects.filter(date__year=timezone.localdate().year - 1):
                try:
                    nd = h.date.replace(year=h.date.year + 1)
                except ValueError:
                    continue
                _, made = Holiday.objects.get_or_create(date=nd, defaults={"name": h.name, "kind": h.kind})
                n += made
            messages.success(request, f"Copied {n} holiday(s) from last year. Check festival dates, they move every year.")
            return redirect("settings")
    hol = []
    for h in Holiday.objects.filter(date__gte=timezone.localdate() - timedelta(days=30)):
        hol.append({"h": h, "clash": PlanEntry.objects.filter(date=h.date).count()})
    return render(request, "founder/settings.html", {"form": form, "hform": hform, "holidays": hol})


@requires("backup.download")
def backup(request):
    """Download every record as one JSON file (restore with `manage.py loaddata`)."""
    record(request, "backup.download", "full JSON backup")
    import io
    from django.core.management import call_command
    from django.http import HttpResponse
    buf = io.StringIO()
    # Records only: photos and the audit trail are left out to stay under the host's
    # 4.5 MB response limit. The weekly GitHub backup (full database) has everything.
    call_command("dumpdata", "ops", "auth.user", natural_foreign=True, stdout=buf,
                 exclude=["ops.storedimage", "ops.auditevent"])
    import gzip
    import json
    rows = json.loads(buf.getvalue())
    for r in rows:                       # drop links to the photos left out, so the file restores cleanly
        for f in ("photos", "photo"):
            r["fields"].pop(f, None)
    resp = HttpResponse(gzip.compress(json.dumps(rows, indent=1).encode()), content_type="application/gzip")
    resp["Content-Disposition"] = f'attachment; filename="studio-ops-backup-{timezone.localdate():%Y-%m-%d}.json.gz"'
    return resp


@requires("settings.manage")
def holiday_delete(request, pk):
    if request.method == "POST":
        Holiday.objects.filter(pk=pk).delete()
        messages.success(request, "Holiday removed.")
    return redirect("settings")


@requires("audit.view")
def audit_log(request):
    from django.core.paginator import Paginator
    from ..models import AuditEvent
    qs = AuditEvent.objects.all()
    q, kind = request.GET.get("q", "").strip(), request.GET.get("kind", "")
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(target__icontains=q) | Q(detail__icontains=q) | Q(action__icontains=q))
    if kind == "security":
        qs = qs.filter(Q(action__startswith="auth.") | Q(action__startswith="access.") | Q(action__startswith="people.")
                       | Q(action="backup.download"))
    elif kind == "changes":
        qs = qs.filter(action__startswith="change.")
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    since = timezone.now() - timedelta(days=7)
    stats = {"failed": AuditEvent.objects.filter(action="auth.login_failed", created__gte=since).count(),
             "denied": AuditEvent.objects.filter(action="access.denied", created__gte=since).count(),
             "locked": AuditEvent.objects.filter(action="auth.locked_out", created__gte=since).count()}
    return render(request, "founder/audit.html", {"page": page, "q": q, "kind": kind, "stats": stats})


@requires("settings.manage")
def access_matrix(request):
    from ..context import ROLE_LABEL
    from ..rbac import PERMISSIONS, ROLE_PERMISSIONS, SENSITIVE
    roles_ = list(ROLE_PERMISSIONS)
    rows = [{"perm": k, "label": v, "sensitive": k in SENSITIVE, "cells": [k in ROLE_PERMISSIONS[r] for r in roles_]}
            for k, v in PERMISSIONS.items()]
    return render(request, "founder/access.html", {"rows": rows, "roles": [ROLE_LABEL[r] for r in roles_]})
