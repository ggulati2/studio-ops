"""Founder (admin) screens: dashboard, projects, planner, deadlines, delivered,
reports, team, extra work, Trailblazer, agencies, settings."""
from collections import defaultdict
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from ..audit import record
from ..rbac import ADMIN, has_perm, requires
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
__all__ = ["backup", "dashboard", "projects", "project_new", "project_edit", "project_status", "project_assign", "deadlines",
           "delivered", "reports", "report_print", "team_view", "person_edit", "person_pod", "extra_admin", "eotm",
           "agencies", "agency_edit", "settings_view", "holiday_delete", "audit_log", "access_matrix"]


def week_load(people, today):
    """Team load this week, straight from the Planner: a working day is "assigned" when a
    film or revisions are planned on it; anything else (Back-up, nothing) is free.
    Every working day assigned = Full; any free day = Can take more."""
    a = week_start(today)
    days = [a + timedelta(days=i) for i in range(7)]
    logs = S._logs_between(days[0], days[-1])
    hols = holidays_between(days[0], days[-1])
    plan = defaultdict(lambda: defaultdict(list))
    for e in (PlanEntry.objects.filter(date__range=(days[0], days[-1]), user__in=people)
              .select_related("project__agency").order_by("date", "slot")):
        plan[e.user_id][e.date].append(e)
    out = []
    for u in people:
        mine = {l.date: l for l in logs.get(u.id, [])}
        cells, work, assigned = [], 0, 0
        for d in days:
            kind = S.day_kind(u, d, mine, hols)
            off = kind in (DayLog.LEAVE, DayLog.HOLIDAY, DayLog.OFF)
            films = [e for e in plan[u.id][d] if e.project_id]
            other = [e.label for e in plan[u.id][d] if not e.project_id and e.label]
            if not off:
                work += 1
                assigned += 1 if films else 0
            label = {DayLog.LEAVE: "Leave", DayLog.HOLIDAY: "Holiday", DayLog.OFF: "Weekly off"}.get(kind, "")
            cells.append({"d": d, "off": off, "assigned": bool(films) and not off, "today": d == today,
                          "past": d < today, "what": " + ".join(e.text for e in films) or ", ".join(other) or label
                          or "Nothing assigned", "backup": bool(other) and not films})
        free = work - assigned
        status = "On leave" if not work else "Full" if not free else "Can take more"
        out.append({"u": u, "cells": cells, "work": work, "assigned": assigned, "free": free, "status": status})
    return out, days


@requires("dashboard.view")
def dashboard(request):
    today = timezone.localdate()
    month = month_start(today)
    open_jobs = (Project.objects.exclude(status__in=(Project.CLOSED, Project.ON_HOLD)).select_related("agency", "editor__profile")
                 .defer("brief", "agency__billing_address", "agency__notes"))
    people = list(team())
    rows, week_days = week_load(people, today)
    counts = defaultdict(int)
    for p in open_jobs:
        if p.editor_id:
            counts[p.editor_id] += 1
    for r in rows:
        r["open"] = counts[r["u"].id]
    delivered = Project.objects.filter(Q(status__in=(Project.CLOSED, Project.CLIENT_REV), closed_on__gte=month)
                                       | Q(status=Project.SENT_AGENCY, first_delivered_on__gte=month))
    on_time = [p for p in delivered if p.deadline]
    rev_by_agency = defaultdict(int)
    for p in delivered.select_related("agency"):
        if p.agency.currency == "INR":
            rev_by_agency[p.agency.name] += p.invoice_amount
    total_rev = sum(rev_by_agency.values()) or 0
    # Revenue estimated: the value of every film logged this month, delivered or not
    est_inr, est_other = 0, defaultdict(int)
    for p in Project.objects.filter(created__date__gte=month).select_related("agency"):
        if p.agency.currency == "INR":
            est_inr += p.invoice_amount
        else:
            est_other[p.agency.currency] += p.invoice_amount
    pipeline = sum(p.invoice_amount for p in open_jobs if p.agency.currency == "INR")
    ctx = {
        "unassigned": open_jobs.filter(editor__isnull=True).count(),
        "overdue": [p for p in open_jobs if p.days_left is not None and p.days_left < 0],
        "due_soon": [p for p in open_jobs if p.days_left is not None and 0 <= p.days_left <= 3],
        "delivered_count": delivered.count(),
        "on_time_pct": round(sum(1 for p in on_time if not p.delivered_late) / len(on_time) * 100) if on_time else None,
        "revenue": total_rev,
        "revenue_est": est_inr,
        "revenue_est_other": sorted(est_other.items()),
        "revenue_est_count": Project.objects.filter(created__date__gte=month).count(),
        "pipeline": pipeline,
        "agencies": sorted(({"name": k, "amount": v, "share": round(v / total_rev * 100) if total_rev else 0}
                            for k, v in rev_by_agency.items()), key=lambda x: -x["amount"]),
        "rows": rows, "week_days": week_days, "week_start": week_days[0],
        "outstanding": sum(i.balance for i in Invoice.objects.exclude(status__in=("paid", "draft")).filter(currency="INR")),
        "award": celebrated_award(),
    }
    return render(request, "founder/dashboard.html", ctx)


def project_filter(g):
    """The Projects page filters, shared by the list, the grouped view and couple blocks.
    Returns (queryset, {"status", "q", "f", "active"})."""
    # long text that list pages never show stays in the database (Supabase bandwidth quota)
    qs = (Project.objects.select_related("agency", "editor__profile", "drive")
          .defer("brief", "agency__billing_address", "agency__notes", "drive__notes"))
    status = g.get("status", "open")
    q = g.get("q", "").strip()
    f = {k: g.get(k, "") for k in ("agency", "editor", "film", "due", "source", "kind", "sort")}
    today = timezone.localdate()
    if status == "open":
        qs = qs.exclude(status__in=(Project.CLOSED, Project.ON_HOLD))
    elif status == "late":
        qs = qs.exclude(status__in=(Project.CLOSED, Project.ON_HOLD, Project.SENT_AGENCY, Project.CLIENT_REV)).filter(deadline__lt=today)
    elif status == "revisions":
        qs = qs.filter(status__in=(Project.TL_REVISIONS, Project.REVISIONS, Project.CLIENT_REV))
    elif status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(couple__icontains=q) | Q(agency__name__icontains=q) | Q(agency__code__iexact=q)
                       | Q(drive__code__icontains=q) | Q(brief__icontains=q))
    if f["agency"].isdigit():
        qs = qs.filter(agency_id=int(f["agency"]))
    if f["editor"] == "none":
        qs = qs.filter(editor__isnull=True)
    elif f["editor"].isdigit():
        qs = qs.filter(editor_id=int(f["editor"]))
    if f["film"] in Project.DELIVERABLES:
        qs = qs.filter(deliverable=f["film"])
    week_end = today + timedelta(days=6 - (today.weekday() + 1) % 7)       # through Saturday
    due = {"overdue": Q(deadline__lt=today) & ~Q(status__in=(Project.CLOSED, Project.ON_HOLD, Project.SENT_AGENCY, Project.CLIENT_REV)), "today": Q(deadline=today), "week": Q(deadline__range=(today, week_end)),
           "14": Q(deadline__range=(today, today + timedelta(days=14))),
           "month": Q(deadline__range=(month_start(today), month_end(today))), "none": Q(deadline__isnull=True),
           "agency_risk": Q(agency_deadline__isnull=False, deadline__gt=models.F("agency_deadline"))}
    if f["due"] in due:
        qs = qs.filter(due[f["due"]])
    if f["source"] == "cloud":
        qs = qs.filter(received_online=True)
    elif f["source"] == "drive":
        qs = qs.filter(drive__isnull=False)
    elif f["source"] == "none":
        qs = qs.filter(drive__isnull=True, received_online=False)
    if f["kind"] == "extra":
        qs = qs.filter(is_extra=True)
    elif f["kind"] == "delayed":
        qs = qs.filter(delays__isnull=False).distinct()
    elif f["kind"] == "client_rev":
        qs = qs.filter(client_revisions__isnull=False).distinct()
    sorts = {"deadline": ("deadline", "couple"), "start": ("start_date", "deadline"), "agency_deadline": ("agency_deadline", "deadline"),
             "newest": ("-created",), "couple": ("couple", "deliverable"), "agency": ("agency__name", "deadline")}
    qs = qs.order_by(*sorts.get(f["sort"], sorts["deadline"]))
    active = sum(1 for k, v in f.items() if v and k != "sort") + (1 if q else 0)
    return qs, {"status": status, "q": q, "f": f, "active": active}


@requires("projects.manage")
def projects(request):
    from .couples import grouped_context
    qs, meta = project_filter(request.GET)
    status, q, f, active = meta["status"], meta["q"], meta["f"], meta["active"]
    view = "list" if request.GET.get("view") == "list" else "grouped"
    switch = {}
    for v in ("grouped", "list"):
        params = request.GET.copy()
        params.pop("show", None)
        params.pop("view", None)
        if v == "list":
            params["view"] = v
        switch[v] = "?" + params.urlencode()
    queue = list(Project.objects.filter(editor__isnull=True).exclude(status__in=(Project.CLOSED, Project.ON_HOLD))
                 .select_related("agency", "drive").defer("brief", "agency__billing_address", "agency__notes", "drive__notes"))
    # Draw PAGE rows at a time; "Show more" adds the next batch without reloading
    try:
        show = max(PAGE, min(int(request.GET.get("show", PAGE)), 5000))
    except ValueError:
        show = PAGE
    from .folders import inbox_context
    ctx = {**inbox_context(), "queue": queue, "status": status, "q": q, "statuses": Project.STATUSES, "f": f, "active_filters": active,
           "agencies": Agency.objects.order_by("name"), "editors": team(), "films": Project.DELIVERABLES,
           "assign_form": AssignForm(), "view": view, "switch": switch}
    if view == "grouped":
        ctx.update(grouped_context(request, qs, meta))
        return render(request, "founder/projects.html", ctx)
    from django.db.models import Sum
    agg = qs.order_by().aggregate(n=Count("id", distinct=True), value=Sum("invoice_amount"))
    total = agg["n"] or 0
    jobs = [{"p": p} for p in S.plan_others(qs[:show])]
    more = None
    if total > show:
        params = request.GET.copy()
        params["show"] = show + PAGE
        more = {"url": "?" + params.urlencode(), "left": total - show, "next": min(PAGE, total - show)}
    ctx.update({"jobs": jobs, "total": total, "more": more, "total_value": agg["value"] or 0})
    return render(request, "founder/projects.html", ctx)


PAGE = 100


def _iso(v):
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def _sent_date(v):
    """The date a film was really sent to the agency (editors sometimes log it a day or two late).
    None = not given (today is used), False = not allowed (in the future)."""
    d = _iso(v)
    if d and d > timezone.localdate():
        return False
    return d


def apply_status(p, new, sent_on=None):
    """Change a film's status. Sent to agency records the real sent date as the delivery date
    (only the first delivery decides on time / late; re-sends after client changes don't)."""
    say = []
    old = p.get_status_display()
    if p.status == new == Project.SENT_AGENCY:
        if p.client_revisions.exists():
            return [("info", f"{p}: the first delivery ({p.first_delivered_on:%-d %b}) decides on time / late, "
                             "and it has been back with the client since, so that date stays.")]
        if sent_on == p.first_delivered_on:
            return []
    # a correction of the sent date while still "Sent to agency" (and never back from the client) counts too
    first_time = not p.first_delivered_on or (p.status == new == Project.SENT_AGENCY and bool(sent_on)
                                               and not p.client_revisions.exists())
    if new == Project.SENT_AGENCY and first_time:
        p.first_delivered_on = sent_on or timezone.localdate()
        p.deadline_at_delivery = p.deadline
    p.status = new
    p.save()
    if new == Project.SENT_AGENCY:
        when = p.first_delivered_on if first_time else (sent_on or timezone.localdate())
        msg = (f"{p}: sent date changed to {when:%-d %b}." if old == p.get_status_display()
               else f"{p}: {old} → Sent to agency on {when:%-d %b}.")
        if first_time and p.delivered_late is not None:
            due = p.agency_deadline or p.deadline
            msg += (f" On time (agency's deadline {due:%-d %b})." if not p.delivered_late
                    else f" Late: the agency's deadline was {due:%-d %b}.")
        elif not first_time:
            msg += f" On time / late stays based on the first delivery ({p.first_delivered_on:%-d %b})."
        say.append(("success", msg))
    else:
        say.append(("success", f"{p}: {old} → {p.get_status_display()}."))
    return say


@requires("projects.manage")
def project_status(request, pk):
    """Change a film's status, deadlines or invoice amount straight from the Projects list."""
    from django.utils.http import url_has_allowed_host_and_scheme
    p = get_object_or_404(Project, pk=pk)
    nxt = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        nxt = reverse("projects")
    if request.method != "POST":
        return redirect(nxt)
    P = request.POST
    say, undo = [], None
    if "status" in P:
        new = P["status"]
        sent_on = _sent_date(P.get("sent_on"))
        if sent_on is False:
            say.append(("error", f"{p}: the sent date can't be in the future."))
        elif new in dict(Project.STATUSES) and (new != p.status or (new == Project.SENT_AGENCY and sent_on)):
            old = p.get_status_display()
            undo = {"status": p.status, "restore_sent": p.first_delivered_on.isoformat() if p.first_delivered_on else "",
                    "restore_due": p.deadline_at_delivery.isoformat() if p.deadline_at_delivery else ""}
            done = apply_status(p, new, sent_on)
            say.extend(done)
            if not any(level == "success" for level, _ in done):
                undo = None
            record(request, "project.status", str(p), f"{old} -> {p.get_status_display()}"
                   + (f" (sent {sent_on:%d %b %Y})" if sent_on and new == Project.SENT_AGENCY else ""))
        if "restore_sent" in P:         # undo of a status change: also put the delivery dates back
            p.first_delivered_on = _iso(P.get("restore_sent"))
            p.deadline_at_delivery = _iso(P.get("restore_due"))
            Project.objects.filter(pk=p.pk).update(first_delivered_on=p.first_delivered_on,
                                                   deadline_at_delivery=p.deadline_at_delivery)
    elif "editor" in P:
        raw = P["editor"]
        new = None if not raw else team().filter(pk=raw).first() if raw.isdigit() else False
        if new is False or (raw and new is None):
            say.append(("error", "Pick an editor from the list."))
        elif (new.pk if new else None) != p.editor_id:
            old = p.editor.profile.name if p.editor_id else "Unassigned"
            undo = {"editor": str(p.editor_id or "")}
            p.editor = new
            p.save(update_fields=["editor"])
            now = new.profile.name if new else "Unassigned"
            record(request, "project.editor", str(p), f"{old} -> {now}")
            say.append(("success", f"{p}: {old} → {now}."))
            if new:
                made, short = S.auto_plan(p)
                if made:
                    say.append(("info", f"Planner updated: {made} day(s) planned for {now}."))
                if short:
                    say.append(("warning", f"{short} day(s) of work didn't fit before the deadline. Check the Planner."))
    elif "invoice_amount" in P:
        raw = P["invoice_amount"].replace(",", "").strip() or "0"
        if not raw.isdigit() or int(raw) > 100_000_000:
            say.append(("error", f"{p}: invoice amount must be a whole number."))
        elif int(raw) != p.invoice_amount:
            old = p.invoice_amount
            undo = {"invoice_amount": str(old)}
            p.invoice_amount = int(raw)
            p.save(update_fields=["invoice_amount"])
            record(request, "project.invoice", str(p), f"{old} -> {p.invoice_amount}")
            say.append(("success", f"{p}: invoice {p.agency.symbol}{old:,} → {p.agency.symbol}{p.invoice_amount:,}."))
    else:
        field = next((f for f in ("start_date", "deadline", "agency_deadline") if f in P), None)
        if field:
            label = {"start_date": "Start date", "deadline": "Editor's deadline", "agency_deadline": "Agency's deadline"}[field]
            try:
                new = date.fromisoformat(P[field]) if P[field] else None
            except ValueError:
                new = False
            ed = new if field == "deadline" else p.deadline
            ad = new if field == "agency_deadline" else p.agency_deadline
            sd = new if field == "start_date" else p.start_date
            if new is False:
                say.append(("error", f"{p}: that isn't a valid date."))
            elif sd and ed and sd > ed:
                say.append(("error", f"{p}: the start date ({sd:%-d %b}) can't be after the editor's deadline ({ed:%-d %b}). Not saved."))
            elif ed and ad and ed > ad:
                say.append(("error", f"{p}: the editor's deadline ({ed:%-d %b}) can't be after the agency's ({ad:%-d %b}). Not saved."))
            elif new != getattr(p, field):
                old = getattr(p, field)
                undo = {field: old.isoformat() if old else ""}
                setattr(p, field, new)
                p.save(update_fields=[field])
                record(request, "project.start" if field == "start_date" else "project.deadline", str(p), f"{label}: {old} -> {new}")
                say.append(("success", f"{p}: {label.lower()} {old:%-d %b} → {new:%-d %b}." if old and new
                                 else f"{p}: {label.lower()} {'set to ' + format(new, '%-d %b') if new else 'cleared'}."))
                if field in ("deadline", "start_date") and p.deadline and p.editor_id:
                    made, short = S.auto_plan(p, trim=True)
                    if made:
                        say.append(("info", f"Planner updated: {made} day(s) planned for {p.editor.profile.name}."))
                    if short:
                        say.append(("warning", f"{short} day(s) of work didn't fit before the deadline. Check the Planner."))
    return _project_reply(request, p, nxt, say, undo)


def _project_reply(request, p, nxt, say, undo=None, remove_queue=False):
    """Fetch requests get JSON with the re-drawn row; plain posts get messages and a redirect."""
    if request.headers.get("X-Requested-With") == "fetch":
        from django.http import JsonResponse
        from django.template.loader import render_to_string
        p = S.plan_others([Project.objects.select_related("agency", "editor__profile", "drive").get(pk=p.pk)])[0]
        layout = "couple" if request.POST.get("layout") == "couple" else ""
        row = render_to_string("founder/_project_row.html", {"p": p, "editors": team(), "statuses": Project.STATUSES,
                                                             "back": nxt, "layout": layout}, request=request)
        bad = any(level == "error" for level, _ in say)
        swap = []
        if layout and p.client_id:      # keep the couple's and agency's summary lines in step
            from .couples import summary_swaps
            swap = summary_swaps(request, p, nxt)
        if undo and layout:
            undo = dict(undo, layout=layout)
        return JsonResponse({"ok": not bad, "pk": p.pk, "row": row, "undo": undo if not bad else None,
                             "undo_url": reverse("project_status", args=[p.pk]), "swap": swap,
                             "messages": [{"level": l, "text": t} for l, t in say],
                             "remove": [f'[data-queue="{p.pk}"]'] if remove_queue else []})
    for level, text in say:
        getattr(messages, level)(request, text)
    return redirect(nxt)


@requires("projects.manage")
def project_new(request):
    from ..forms import apply_source
    form = NewProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        c = form.cleaned_data
        editor = User.objects.filter(pk=c["editor"]).first() if c["editor"] else None
        rates = {r.deliverable: r.amount for r in c["agency"].rates.all()}
        made, short, jobs = 0, 0, []
        with transaction.atomic():
            for d, editor_deadline, agency_deadline, amount in form.films():
                job = Project(
                    couple=c["couple"], deliverable=d, agency=c["agency"], editor=editor,
                    deadline=editor_deadline, agency_deadline=agency_deadline, brief=c["brief"],
                    start_date=c.get("start_date"),
                    invoice_amount=amount if amount is not None else rates.get(d, 0),
                    is_extra=c["is_extra"] and bool(editor), extra_basis=c["extra_basis"] or "fixed",
                    extra_rate=c["extra_rate"] or 0)
                apply_source(job, c["source"], c["cloud_link"])
                job.save()
                jobs.append(job)
            for job in sorted(jobs, key=lambda j: (j.deadline or date.max)):   # earliest deadline gets planned first
                m, sh = S.auto_plan(job)
                made, short = made + m, short + sh
        messages.success(request, f"Created {len(jobs)} job(s) for {c['couple']}."
                         + (f" Planned {made} day(s) for {editor.profile.name} in the Planner." if made else ""))
        if short:
            messages.warning(request, f"{editor.profile.name}'s planner is full before the deadline: {short} more day(s) "
                                      "of work didn't fit. Check the Planner.")
        return redirect("projects")
    import json
    rates = {a.pk: {r.deliverable: r.amount for r in a.rates.all()} for a in Agency.objects.filter(active=True)}
    return render(request, "founder/project_new.html", {"form": form, "rates_json": json.dumps(rates)})


@requires("projects.manage")
def project_edit(request, pk):
    p = get_object_or_404(Project, pk=pk)
    form = ProjectForm(request.POST or None, instance=p)
    if request.method == "POST" and form.is_valid():
        before = Project.objects.values("editor_id", "deadline", "start_date").get(pk=p.pk)
        obj = form.save(commit=False)
        if obj.status == Project.CLOSED and not obj.closed_on:
            obj.closed_on = timezone.localdate()
        obj.save()
        if (before["editor_id"], before["deadline"], before["start_date"]) != (obj.editor_id, obj.deadline, obj.start_date):
            made, short = S.auto_plan(obj, trim=before["editor_id"] == obj.editor_id)
            if made:
                messages.info(request, f"Planner updated: {made} day(s) planned for {obj.editor.profile.name}.")
            if short:
                messages.warning(request, f"{short} day(s) of work didn't fit before the deadline. Check the Planner.")
        messages.success(request, "Job saved.")
        return redirect("projects")
    return render(request, "founder/project_edit.html", {"form": form, "p": p, "hours": project_hours(p),
                                                       "reviews": p.reviews.select_related("reviewer")})


@requires("projects.manage")
def project_assign(request, pk):
    from django.utils.http import url_has_allowed_host_and_scheme
    p = get_object_or_404(Project, pk=pk)
    nxt = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        nxt = reverse("projects")
    form = AssignForm(request.POST)
    say, undo, assigned = [], None, False
    if form.is_valid():
        undo = {"editor": str(p.editor_id or "")}
        p.editor = User.objects.get(pk=form.cleaned_data["editor"])
        p.save(update_fields=["editor"])
        assigned = True
        record(request, "project.editor", str(p), f"Unassigned -> {p.editor.profile.name}")
        made, short = S.auto_plan(p)
        say.append(("success", f"{p} assigned to {p.editor.profile.name}."
                    + (f" {made} day(s) added to the Planner." if made else "")))
        if short:
            say.append(("warning", f"{short} day(s) of work didn't fit before the deadline. Check the Planner."))
    else:
        say.append(("error", "Pick an editor."))
    return _project_reply(request, p, nxt, say, undo, remove_queue=assigned)


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
    if request.method == "POST":           # change a delivery tag inline
        p = get_object_or_404(Project, pk=request.POST.get("project"))
        p.timing_override = request.POST.get("timing_override", "") if request.POST.get("timing_override", "") in dict(Project.TIMING) else ""
        p.timing_note = request.POST.get("timing_note", p.timing_note)[:200]
        p.save(update_fields=["timing_override", "timing_note"])
        messages.success(request, f"{p}: {p.timing_label}.")
        return redirect(request.get_full_path())
    qs = (Project.objects.filter(status__in=(Project.CLOSED, Project.CLIENT_REV), closed_on__range=(month_start(month), month_end(month)))
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
        rows.append({"p": p, "qc_rounds": p.reviews.filter(kind="qc").count(), "invoice": inv,
                     "revisions": p.client_revisions.count()})
    pending_close = Project.objects.filter(status=Project.QC_PASSED).select_related("editor__profile")
    from ..models import HardDrive
    drives = []
    for d in HardDrive.objects.exclude(status="sent").prefetch_related("projects"):
        jobs = list(d.projects.all())
        if jobs:
            open_ = [j for j in jobs if j.status != Project.CLOSED]
            drives.append({"d": d, "jobs": len(jobs), "open": open_, "safe": not open_})
    return render(request, "founder/delivered.html", {"rows": rows, "timing_choices": Project.TIMING, "month": month, "prev": add_months(month, -1),
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
    today = timezone.localdate()
    a, b = month_start(month), month_end(month)
    days = [a + timedelta(days=i) for i in range((b - a).days + 1)]
    hols = holidays_between(a, b)
    from ..services import _logs_between, day_kind
    all_logs = _logs_between(a, b)
    rows = []
    for u in User.objects.filter(profile__isnull=False).exclude(profile__role=Profile.ADMIN).select_related("profile", "profile__pod").order_by("profile__pod__name", "first_name"):
        att = attendance(u, month)
        avail = available_days(u, a, min(b, today)) * hpd
        value = values.get(u.id, 0)
        sal = u.profile.monthly_salary
        logs = {l.date: l for l in all_logs.get(u.id, [])}
        # Day by day: productive hours, and working days that passed with nothing logged.
        # Contract editors don't log themselves, so they're never "missing".
        track = u.profile.active and not u.profile.is_contract and has_perm(u, "work.log_own")
        strip, missing = [], []
        for d in days:
            log = logs.get(d)
            kind = day_kind(u, d, logs, hols)
            miss = track and not log and d < today and kind not in (DayLog.OFF, DayLog.HOLIDAY)
            if miss:
                missing.append(d)
            h = float(log.hours) if log and log.hours else 0.0
            strip.append({"d": d, "h": h, "kind": "" if kind in ("", DayLog.OFFICE) else kind, "log": log,
                          "missing": miss, "future": d > today, "level": min(4, int(h / hpd * 4 + 0.5)) if h else 0})
        worked = sum(1 for c in strip if c["h"])
        rows.append({"u": u, "att": att, "avail": round(avail, 1),
                     "util": round(att["job_hours"] / avail * 100) if avail else None,
                     "value": value, "multiple": round(value / sal, 1) if sal else None,
                     "missing": missing, "strip": strip, "worked": worked,
                     "avg": round(att["hours"] / worked, 1) if worked else None,
                     "target": round(worked * hpd, 1)})
    return render(request, "founder/team.html", {
        "rows": rows, "month": month, "prev": add_months(month, -1), "next": add_months(month, 1),
        "pods": list(Pod.objects.all()), "can_edit_people": has_perm(request.user, "people.manage"),
        "days": [{"d": d, "hol": hols.get(d), "off": d.weekday() not in StudioSettings.get().weekdays,
                  "today": d == today} for d in days],
        "hpd": hpd, "missing_total": sum(len(r["missing"]) for r in rows),
        "team_hours": round(sum(r["att"]["hours"] for r in rows), 1)})


@requires("people.manage")
def person_pod(request, pk):
    """Change someone's pod from the Team page (or create a new pod on the way)."""
    from django.http import JsonResponse
    u = get_object_or_404(User.objects.select_related("profile", "profile__pod"), pk=pk, profile__isnull=False)
    fetch = request.headers.get("X-Requested-With") == "fetch"

    def reply(ok, msg, undo=None, status=200):
        if fetch:
            return JsonResponse({"ok": ok, "message": msg, "undo": undo, "pod_id": u.profile.pod_id,
                                 "pods": [{"id": p.pk, "name": p.name} for p in Pod.objects.all()]}, status=status)
        (messages.success if ok else messages.error)(request, msg)
        return redirect("team")

    if request.method != "POST":
        return redirect("team")
    raw = request.POST.get("pod", "")
    new_name = " ".join(request.POST.get("new_pod", "").split())[:40]
    if raw == "__new__":
        if not new_name:
            return reply(False, "Type a name for the new pod.")
        pod = Pod.objects.filter(name__iexact=new_name).first()
        if not pod:
            pod = Pod.objects.create(name=new_name)
            record(request, "pod.created", pod.name)
    elif raw:
        pod = Pod.objects.filter(pk=raw).first() if raw.isdigit() else None
        if not pod:
            return reply(False, "That pod no longer exists. Reload the page.")
    else:
        pod = None
    old = u.profile.pod
    if (old and old.pk) == (pod and pod.pk):
        return reply(True, "")
    u.profile.pod = pod
    u.profile.save(update_fields=["pod"])
    record(request, "person.pod", u.username, f"{old or 'no pod'} -> {pod or 'no pod'}")
    name = u.profile.name
    msg = f"{name} moved to {pod}." if pod else f"{name} is no longer in a pod."
    if pod and raw == "__new__":
        msg = f"Created {pod} and moved {name} into it."
    return reply(True, msg, undo={"pod": str(old.pk) if old else ""})


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
            contract = c["employment"] == Profile.CONTRACT
            if contract:
                user.set_unusable_password()      # contract editors are managed here but never sign in
            elif c["password"]:
                user.set_password(c["password"])
            user.save()
            p, _ = Profile.objects.get_or_create(user=user)
            p.role, p.pod, p.monthly_salary, p.joined_on, p.active = c["role"], c["pod"], c["monthly_salary"], c["joined_on"], c["active"]
            p.employment = c["employment"]
            p.contract_basis = c["contract_basis"] if p.is_contract else ""
            p.contract_rate = (c["contract_rate"] or 0) if p.is_contract else 0
            if p.is_contract:
                p.monthly_salary = 0
            if c["password"] and user.pk != request.user.pk and not contract:
                p.must_change_password = True   # a password someone else knows must be replaced at first login
            p.save()
        record(request, "people.create" if created else "people.update", user.username,
               f"role {old_role or '-'} -> {c['role']}; active={c['active']}"
               + ("; contract, no login" if contract else "; password set" if c["password"] else ""))
        if c["password"] and user.pk == request.user.pk:
            from django.contrib.auth import update_session_auth_hash
            update_session_auth_hash(request, user)
        if contract:
            messages.success(request, f"Saved {user.profile.name} (contract, no portal login). Log their work and payments under Contract editors.")
            return redirect("contractor_detail", pk=user.pk)
        if c["password"] and user.pk != request.user.pk:
            # Show the login details once, on this response only (never stored or put in a cookie).
            resp = render(request, "founder/person_saved.html", {
                "person": user, "login_id": user.username, "password": c["password"], "created": created,
                "app_url": request.build_absolute_uri("/")})
            resp["Cache-Control"] = "no-store"
            return resp
        messages.success(request, f"Saved {user.profile.name}.")
        return redirect("team")
    from ..models import TwoFactor
    return render(request, "founder/person_form.html", {
        "form": form, "person": user, "title": f"Edit {user.profile.name}" if user else "Add a person",
        "two_step_on": bool(user and TwoFactor.objects.filter(user=user, enabled_on__isnull=False).exists())})


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
        messages.success(request, f"{obj.user.profile.name} is Trailblazer of the Month for {obj.month:%B %Y}.")
        return redirect("eotm")
    # Suggestions for last month: numbers only, the choice stays with the founders.
    last = add_months(month_start(timezone.localdate()), -1)
    sugg = []
    for u in team():
        closed = Project.objects.filter(editor=u, status=Project.CLOSED, closed_on__range=(last, month_end(last)))
        on_time = [p for p in closed if p.deadline and not p.delivered_late]
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
    """Download every record as one JSON file (restore with `manage.py loaddata`).
    The file holds salaries, bank details and invoices, so the founder confirms their
    password first, and password hashes are left out of it."""
    from django.core.cache import cache
    key = f"backup-pw:{request.user.pk}"
    if request.method != "POST":
        return render(request, "founder/backup_confirm.html", {})
    if cache.get(key, 0) >= 5:
        messages.error(request, "Too many wrong passwords. Try again in 15 minutes.")
        return redirect("settings")
    if not request.user.check_password(request.POST.get("password", "")):
        cache.set(key, cache.get(key, 0) + 1, 15 * 60)
        record(request, "backup.denied", "wrong password")
        return render(request, "founder/backup_confirm.html", {"error": True}, status=403)
    cache.delete(key)
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
        if r.get("model") == "auth.user":   # never put password hashes in a file on someone's laptop
            r["fields"]["password"] = "!"
    rows = [r for r in rows if r.get("model") != "ops.twofactor"]
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
