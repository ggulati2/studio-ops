"""Planner: who works on what, day by day.

Two views share the same cells and editing:
- 4 weeks: one row per day, one column per editor
- Month:   one row per editor, one column per day

Every change is saved in the background (fetch) and only the changed cells are
re-drawn, so the page never reloads. Plain form posts still work (redirect back)."""
from collections import defaultdict
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from .. import services as S
from ..models import DayLog, PlanEntry, Project, StudioSettings
from ..rbac import requires
from ..services import add_months, holidays_between, month_end, month_start, parse_month, team, week_start

__all__ = ["planner"]
User = get_user_model()
WEEKS = 4
LOCKED = (DayLog.LEAVE,)          # nothing can be planned on approved / logged leave


def _is_ajax(request):
    return request.headers.get("X-Requested-With") == "fetch"


class _Ctx:
    """Everything needed to draw cells for a set of people over a date range."""

    def __init__(self, people, a, b):
        self.people, self.a, self.b = people, a, b
        self.plan = defaultdict(dict)
        for e in PlanEntry.objects.filter(date__range=(a, b), user__in=people).select_related("project__agency"):
            self.plan[(e.user_id, e.date)][e.slot] = e
        self.logs = defaultdict(dict)
        for l in DayLog.objects.filter(date__range=(a, b), user__in=people):
            self.logs[l.user_id][l.date] = l
        self.hols = holidays_between(a, b)
        self.due = S.deliveries_between(a, b)

    def cell(self, u, d):
        kind = S.day_kind(u, d, self.logs[u.id], self.hols)
        if kind in (DayLog.OFFICE, DayLog.DOWNTIME):
            kind = ""
        slots = self.plan.get((u.id, d), {})
        return {"u": u, "d": d, "entry": slots.get(1), "also": slots.get(2), "kind": kind,
                "locked": kind in LOCKED, "off": kind in (DayLog.LEAVE, DayLog.HOLIDAY, DayLog.OFF),
                "due": [p for p in self.due.get(d, []) if p.editor_id == u.id]}


def _options():
    open_projects = list(Project.objects.exclude(status__in=(Project.CLOSED, Project.ON_HOLD))
                         .select_related("agency", "editor__profile").order_by("deadline", "couple"))
    delivered = list(Project.objects.filter(status__in=(Project.CLOSED, Project.SENT_AGENCY),
                                            first_delivered_on__gte=timezone.localdate() - timedelta(days=120))
                     .select_related("agency").order_by("-first_delivered_on"))
    return open_projects, delivered


def _cell_payload(ctx, u, d, view):
    c = ctx.cell(u, d)
    tpl = "founder/_plan_cell_month.html" if view == "month" else "founder/_plan_cell.html"
    cls = " ".join(x for x in ["k-" + c["kind"] if c["kind"] else "", "has" if c["entry"] else "",
                               "due" if c["due"] else "", "locked" if c["locked"] else ""] if x)
    return {"html": render_to_string(tpl, {"c": c}), "cls": cls}


def _parse_date(v):
    try:
        return date.fromisoformat(v)
    except (TypeError, ValueError):
        return None


def _value_label(value):
    f = S.plan_fields(value)
    if f["project_id"]:
        p = Project.objects.filter(pk=f["project_id"]).first()
        return f"Revisions: {p}" if f["label"] == "Revisions" else str(p)
    return f["label"] or "nothing"


def _post(request):
    """Apply one planner change. Returns (changed [(user, date)], message, undo-or-None)."""
    P = request.POST
    action = P.get("action", "set")
    if action == "move":
        src_u = get_object_or_404(User, pk=P.get("from_user"))
        dst_u = get_object_or_404(User, pk=P.get("to_user"))
        src_d, dst_d = _parse_date(P.get("from_date")), _parse_date(P.get("to_date"))
        src_slot = 2 if P.get("from_slot") == "2" else 1
        if not (src_d and dst_d):
            return [], "That date isn't valid.", None
        e = PlanEntry.objects.filter(user=src_u, date=src_d, slot=src_slot).select_related("project").first()
        if not e:
            return [], "Nothing to move there any more.", None
        if S.day_kind(dst_u, dst_d) in LOCKED:
            return [], f"{dst_u.profile.name} is on leave on {dst_d:%-d %b}.", None
        value, text = e.value, e.text
        copy = P.get("copy") == "1"
        same_cell = (src_u.pk, src_d) == (dst_u.pk, dst_d)
        if same_cell:
            return [], "", None
        dst_slots = {x.slot: x for x in PlanEntry.objects.filter(user=dst_u, date=dst_d)}
        slot = 1 if 1 not in dst_slots else 2
        S.set_plan(dst_u, dst_d, value, slot)
        if not copy:
            S.set_plan(src_u, src_d, "", src_slot)
        verb = "Copied" if copy else "Moved"
        where = "alongside other work" if slot == 2 else ""
        msg = f"{verb} {text} to {dst_u.profile.name}, {dst_d:%a %-d %b}" + (f" ({where})" if where else "") + "."
        return [(src_u, src_d), (dst_u, dst_d)], msg, None

    u = get_object_or_404(User, pk=P.get("user"))
    d = _parse_date(P.get("date"))
    if not d:
        return [], "That date isn't valid.", None
    val = (P.get("value") or "").strip()
    slot = "2" if P.get("slot") == "2" else "1"
    if S.day_kind(u, d) in LOCKED and val:
        return [], f"{u.profile.name} is on leave on {d:%-d %b}.", None
    try:  # only when the Copy button itself was pressed (not when a select auto-saves)
        repeat = max(0, min(int(P.get("repeat", "0") or 0), 60)) if (P.get("repeat_go") or action == "repeat") else 0
    except ValueError:
        repeat = 0
    if repeat and val:
        done, last = S.repeat_plan(u, d, val, repeat, slot)
        changed = [(u, d + timedelta(days=i)) for i in range(1, (last - d).days + 1)]
        return changed, (f"Copied to {done} more working day(s) for {u.profile.name}, up to {last:%-d %b} "
                         "(weekly offs, holidays and leave skipped)."), None
    before = PlanEntry.objects.filter(user=u, date=d, slot=int(slot)).first()
    before_also = PlanEntry.objects.filter(user=u, date=d, slot=2).first() if slot == "1" and not val else None
    S.set_plan(u, d, val, slot)
    undo = {"user": u.pk, "date": d.isoformat(), "slot": slot, "value": before.value if before else ""}
    if before_also:   # clearing the main slot promoted "alongside"; undo puts both back
        undo = {"user": u.pk, "date": d.isoformat(), "restore": [before.value if before else "", before_also.value]}
    who = f"{u.profile.name}, {d:%a %-d %b}"
    if val:
        msg = f"{_value_label(val)} planned for {who}" + (" (alongside)." if slot == "2" else ".")
    else:
        msg = f"Cleared {who}."
    return [(u, d)], msg, undo


@requires("planner.manage")
def planner(request):
    view = "month" if (request.GET.get("view") or request.POST.get("view")) == "month" else "weeks"
    if request.method == "POST":
        if request.POST.get("restore") is not None or request.POST.getlist("restore"):
            # Undo of a clear that had promoted the alongside slot: put both slots back as they were
            u = get_object_or_404(User, pk=request.POST.get("user"))
            d = _parse_date(request.POST.get("date"))
            vals = request.POST.getlist("restore")
            if d:
                PlanEntry.objects.filter(user=u, date=d).delete()
                for i, v in enumerate(vals[:2], start=1):
                    if v:
                        PlanEntry.objects.create(user=u, date=d, slot=i, **S.plan_fields(v))
            changed, msg, undo = ([(u, d)] if d else []), "Undone.", None
        else:
            changed, msg, undo = _post(request)
        if _is_ajax(request):
            changed = [(u, d) for u, d in dict.fromkeys(changed)]
            out = {}
            if changed:
                ctx = _Ctx([u for u, _ in changed], min(d for _, d in changed), max(d for _, d in changed))
                for u, d in changed:
                    out[f"{u.pk}:{d.isoformat()}"] = _cell_payload(ctx, u, d, view)
            return JsonResponse({"ok": bool(changed) or not msg, "message": msg, "cells": out, "undo": undo})
        if msg:
            messages.success(request, msg)
        return redirect(S.safe_next(request, request.POST.get("back"), request.get_full_path()))
    return _month(request) if view == "month" else _weeks(request)


def _common(people):
    open_projects, delivered = _options()
    return {"open_projects": open_projects, "delivered": delivered, "people": people,
            "tray": [p for p in open_projects if not p.editor_id], "today": timezone.localdate()}


def _weeks(request):
    start = _parse_date(request.GET.get("start")) or timezone.localdate()
    start = week_start(start)
    days = [start + timedelta(days=i) for i in range(7 * WEEKS)]
    people = list(team())
    ctx = _Ctx(people, days[0], days[-1])
    grid = []
    for d in days:
        grid.append({"d": d, "cells": [ctx.cell(u, d) for u in people], "holiday": ctx.hols.get(d),
                     "due": ctx.due.get(d, []), "week_break": d.weekday() == start.weekday() and d != start})
    data = _common(people)
    data.update({"grid": grid, "start": start, "end": days[-1], "prev": start - timedelta(days=7 * WEEKS),
                 "next": start + timedelta(days=7 * WEEKS), "this_week": week_start(timezone.localdate()),
                 "view": "weeks"})
    return render(request, "founder/planner.html", data)


def _month(request):
    month = parse_month(request.GET.get("m"))
    a, b = month_start(month), month_end(month)
    days = [a + timedelta(days=i) for i in range((b - a).days + 1)]
    people = list(team())
    ctx = _Ctx(people, a, b)
    today = timezone.localdate()
    rows = []
    for u in people:
        cells, planned, workdays = [], 0, 0
        for d in days:
            c = ctx.cell(u, d)
            if not c["off"]:
                workdays += 1
                planned += 1 if c["entry"] else 0
            cells.append(c)
        rows.append({"u": u, "cells": cells, "planned": planned, "workdays": workdays, "free": workdays - planned})
    data = _common(people)
    data.update({"rows": rows, "days": [{"d": d, "holiday": ctx.hols.get(d), "due": len(ctx.due.get(d, [])), "today": d == today,
                                         "week": week_start(d)} for d in days],
                 "month": month, "prev": add_months(month, -1), "next": add_months(month, 1), "view": "month"})
    return render(request, "founder/planner_month.html", data)
