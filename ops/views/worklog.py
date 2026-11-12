"""Work log: one editor's whole month, day by day: what they logged against which film
(stage, hours), their day type and in/out times, what the Planner had for them, after-hours
extra work, and the month's hours per film. Founders only (anyone allowed to see everyone's
attendance). Filters redraw in place; CSV for payroll checks."""
import csv
from collections import defaultdict
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from .. import services as S
from ..models import DayLog, ExtraEntry, PlanEntry
from ..rbac import requires
from ..services import add_months, holidays_between, month_end, month_start, parse_month, team

__all__ = ["work_log"]
User = get_user_model()
OFF_KINDS = (DayLog.LEAVE, DayLog.HOLIDAY, DayLog.OFF)


def _month(u, month):
    a, b = month_start(month), month_end(month)
    today = timezone.localdate()
    logs = {l.date: l for l in DayLog.objects.filter(user=u, date__range=(a, b))
            .prefetch_related("tasks__project__agency")}
    plans = defaultdict(list)
    for e in PlanEntry.objects.filter(user=u, date__range=(a, b)).select_related("project__agency").order_by("slot"):
        plans[e.date].append(e)
    extras = defaultdict(list)
    for x in ExtraEntry.objects.filter(user=u, date__range=(a, b)).select_related("project__agency").order_by("start"):
        extras[x.date].append(x)
    hols = holidays_between(a, b)
    days, films = [], {}
    tot = {"hours": 0.0, "logged": 0, "missing": 0, "workdays": 0, "leave": 0, "extra": 0.0, "late": 0}
    d = a
    while d <= b:
        log = logs.get(d)
        kind = S.day_kind(u, d, logs, hols)
        tasks = []
        if log:
            sh = S.split_hours(log)
            for t in log.tasks.all():
                h = sh.get(t.id, 0)
                tasks.append({"p": t.project, "stage": t.get_stage_display(), "h": h, "typed": t.hours is not None})
                f = films.setdefault(t.project_id, {"p": t.project, "h": 0.0, "days": set(), "stages": set(), "extra": 0.0})
                f["h"] += h
                f["days"].add(d)
                f["stages"].add(t.get_stage_display())
        xs = []
        for x in extras.get(d, []):
            xs.append({"p": x.project, "stage": x.get_stage_display(), "h": round(x.hours, 2),
                       "when": f"{x.start:%H:%M}–{x.end:%H:%M}", "note": x.note})
            f = films.setdefault(x.project_id, {"p": x.project, "h": 0.0, "days": set(), "stages": set(), "extra": 0.0})
            f["extra"] += x.hours
            tot["extra"] += x.hours
        off = kind in OFF_KINDS
        workday = not off and d <= today
        missing = workday and not log
        planned = [e for e in plans.get(d, [])]
        logged_ids = {t["p"].pk for t in tasks}
        planned_ids = {e.project_id for e in planned if e.project_id}
        if workday:
            tot["workdays"] += 1
        if log and not off:
            tot["logged"] += 1
            tot["hours"] += log.hours
            tot["late"] += 1 if log.is_late else 0
        if kind == DayLog.LEAVE:
            tot["leave"] += 1
        tot["missing"] += 1 if missing else 0
        days.append({
            "d": d, "log": log, "kind": kind, "kind_label": dict(DayLog.DAY_TYPES).get(kind, "") if kind else "",
            "off": off, "future": d > today, "missing": missing, "today": d == today, "tasks": tasks, "extras": xs,
            "planned": planned, "holiday": hols.get(d),
            "unlogged_hours": round(log.hours - sum(t["h"] for t in tasks), 2) if log and tasks else (log.hours if log else 0),
            "off_plan": bool(tasks and planned_ids and not (logged_ids & planned_ids)),
        })
        d += timedelta(days=1)
    film_rows = sorted(({**f, "days": len(f["days"]), "stages": sorted(f["stages"]), "h": round(f["h"], 2),
                         "extra": round(f["extra"], 2)} for f in films.values()), key=lambda f: -(f["h"] + f["extra"]))
    tot = {k: round(v, 2) if isinstance(v, float) else v for k, v in tot.items()}
    return days, film_rows, tot


@requires("attendance.view_all")
def work_log(request):
    people = list(team())
    try:
        pick = int(request.GET.get("u") or 0)
    except ValueError:
        pick = 0
    u = next((p for p in people if p.pk == pick), people[0] if people else None)
    month = parse_month(request.GET.get("m"))
    if not u:
        return render(request, "founder/work_log.html", {"people": people, "month": month})
    days, films, tot = _month(u, month)
    if request.GET.get("format") == "csv":
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="work-log-{u.username}-{month:%Y-%m}.csv"'
        w = csv.writer(resp)
        w.writerow(["Date", "Day", "In", "Out", "Productive hours", "Film", "Stage", "Hours on film", "Extra (after hours)", "Notes"])
        for x in days:
            log = x["log"]
            base = [x["d"].isoformat(), "Not logged" if x["missing"] else x["kind_label"],
                    f"{log.time_in:%H:%M}" if log and log.time_in else "", f"{log.time_out:%H:%M}" if log and log.time_out else "",
                    log.hours if log else ""]
            rows = [[str(t["p"]), t["stage"], t["h"], "", ""] for t in x["tasks"]] + \
                   [[str(e["p"]), e["stage"], "", e["h"], e["note"]] for e in x["extras"]]
            for i, r in enumerate(rows or [["", "", "", "", ""]]):
                w.writerow((base if i == 0 else [x["d"].isoformat(), "", "", "", ""]) + r[:4] + [log.notes if (log and i == 0) else r[4]])
        return resp
    params = request.GET.copy()
    nav = {}
    for k, m in (("prev", add_months(month, -1)), ("next", add_months(month, 1))):
        q = params.copy()
        q["m"] = f"{m:%Y-%m}"
        nav[k] = "?" + q.urlencode()
    csv_q = params.copy()
    csv_q["format"] = "csv"
    csv_q["u"] = u.pk
    csv_q["m"] = f"{month:%Y-%m}"
    return render(request, "founder/work_log.html", {
        "people": people, "u": u, "month": month, "days": days, "films": films, "tot": tot, "nav": nav,
        "csv_url": "?" + csv_q.urlencode(), "this_month": month == month_start(timezone.localdate())})
