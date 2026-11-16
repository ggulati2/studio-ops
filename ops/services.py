"""Business rules. Views call these; nothing here touches the request."""
import calendar
import io
import threading
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.db import models
from django.utils import timezone

from .models import (DayLog, EmployeeOfMonth, ExtraEntry, Holiday, Invoice, Profile, Project,
                     StoredImage, StudioSettings, TaskEntry, TaskStage)

User = get_user_model()


# ---------------------------------------------------------------- dates
def month_start(d):
    return d.replace(day=1)


def month_end(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d, n):
    m = d.month - 1 + n
    y = d.year + m // 12
    return date(y, m % 12 + 1, 1)


def parse_month(value, default=None):
    """'2026-11' -> date(2026, 11, 1)."""
    try:
        y, m = value.split("-")
        return date(int(y), int(m), 1)
    except (AttributeError, ValueError):
        return default or month_start(timezone.localdate())


def week_start(d):
    return d - timedelta(days=d.weekday())


# ---------------------------------------------------------------- people
def team(roles=(Profile.TL, Profile.EDITOR, Profile.INTERN)):
    return (User.objects.filter(profile__role__in=roles, profile__active=True, is_active=True)
            .select_related("profile", "profile__pod").order_by("profile__pod__name", "first_name"))


def role_of(user):
    from .rbac import role_of as _r
    return _r(user)


# ---------------------------------------------------------------- hours
def split_hours(daylog):
    """Hours for each task of a day. Typed hours are kept; blank ones share
    what's left of the in/out hours equally (never below zero)."""
    tasks = list(daylog.tasks.all())
    typed = sum(float(t.hours) for t in tasks if t.hours)
    blanks = [t for t in tasks if not t.hours]
    left = max(daylog.hours - typed, 0)
    share = left / len(blanks) if blanks else 0
    return {t.id: (float(t.hours) if t.hours else round(share, 2)) for t in tasks}


def task_hours(qs_daylogs):
    """{(user_id, project_id): hours} for the given day logs."""
    out = defaultdict(float)
    for log in qs_daylogs.prefetch_related("tasks"):
        for t_id, h in split_hours(log).items():
            t = next(t for t in log.tasks.all() if t.id == t_id)
            out[(log.user_id, t.project_id)] += h
    return out


def all_project_hours():
    """{project_id: hours} for every film, in two queries (memoised per request)."""
    from .perf import memo

    def compute():
        out = defaultdict(float)
        # only the columns the sums need: this runs over every day ever logged
        tasks = TaskEntry.objects.only("id", "daylog_id", "project_id", "hours")
        logs = (DayLog.objects.filter(tasks__isnull=False).distinct()
                .only("id", "date", "time_in", "time_out", "day_type", "user_id")
                .prefetch_related(models.Prefetch("tasks", queryset=tasks)))
        for log in logs:
            sh = split_hours(log)
            for t in log.tasks.all():
                out[t.project_id] += sh.get(t.id, 0)
        for e in ExtraEntry.objects.only("project_id", "date", "start", "end"):
            out[e.project_id] += e.hours
        return {k: round(v, 1) for k, v in out.items()}

    def load():
        # Shared across server processes; dropped whenever a day, task, extra entry or film changes
        from django.core.cache import cache
        val = cache.get("hours_all")
        if val is None:
            val = compute()
            cache.set("hours_all", val, 60 * 30)
        return val
    return memo("hours_all", load)


def project_hours(project):
    from .perf import peek
    cached = peek("hours_all")
    if cached is not None:
        return cached.get(project.id, 0.0)
    total = 0.0
    for log in DayLog.objects.filter(tasks__project=project).distinct().prefetch_related("tasks"):
        sh = split_hours(log)
        total += sum(h for t_id, h in sh.items()
                     if next(t for t in log.tasks.all() if t.id == t_id).project_id == project.id)
    total += sum(e.hours for e in project.extra_entries.all())
    return round(total, 1)


def benchmarks():
    """Average hours of delivered films by deliverable."""
    from .perf import memo
    return memo("benchmarks", _benchmarks)


def _benchmarks():
    sums = defaultdict(lambda: [0.0, 0])
    hours = all_project_hours()
    for p in Project.objects.filter(status=Project.CLOSED).only("id", "deliverable"):
        h = hours.get(p.id, 0)
        if h:
            sums[p.deliverable][0] += h
            sums[p.deliverable][1] += 1
    return {k: round(v[0] / v[1], 1) for k, v in sums.items()}


# Starting estimates (hours) until the studio has its own delivered films of a type.
# Your real averages replace these.
DEFAULT_HOURS = {"Teaser": 10, "Highlight": 24, "Trailer": 20, "Full Film": 30, "Traditional Film": 22, "Reels": 3,
                 "Same Day Edit": 8, "Event Video": 16, "Ad / Corporate": 20, "Cleanup Shots": 4, "Other": 8}


def usual_hours(project, bench=None):
    bench = bench if bench is not None else benchmarks()
    return bench.get(project.deliverable) or float(DEFAULT_HOURS.get(project.deliverable, 8))


def hours_remaining(project, bench=None):
    usual = usual_hours(project, bench)
    if not usual or not project.is_open:
        return 0.0
    return max(usual - project_hours(project), 0.0)


# ---------------------------------------------------------------- calendar / leave
def holidays_between(a, b):
    from .perf import memo
    return dict(memo(("hol", a, b), lambda: {h.date: h for h in Holiday.objects.filter(date__range=(a, b))}))


def _logs_between(a, b):
    """{user_id: [DayLog…]} for a date range, with tasks, fetched once per request."""
    from .perf import memo

    def load():
        out = defaultdict(list)
        for l in DayLog.objects.filter(date__range=(a, b)).prefetch_related("tasks"):
            out[l.user_id].append(l)
        return out
    return memo(("logs", a, b), load)


def day_kind(user, d, logs=None, hols=None):
    """What a day is for a person: the logged day type, else holiday / weekly off / '' (normal)."""
    logs = logs if logs is not None else {l.date: l for l in DayLog.objects.filter(user=user, date=d)}
    if d in logs:
        return logs[d].day_type
    hols = hols if hols is not None else holidays_between(d, d)
    if d in hols and hols[d].kind != Holiday.OPTIONAL:
        return DayLog.HALF if hols[d].kind == Holiday.HALF else DayLog.HOLIDAY
    if d.weekday() not in StudioSettings.get().weekdays:
        return DayLog.OFF
    return ""


def leave_count(user, month):
    a, b = month_start(month), month_end(month)
    logs = _logs_between(a, b).get(user.id, [])
    full = sum(1 for l in logs if l.day_type == DayLog.LEAVE)
    hols = holidays_between(a, b)
    half = sum(1 for l in logs if l.day_type == DayLog.HALF and not (l.date in hols and hols[l.date].kind == Holiday.HALF))
    return Decimal(full) + Decimal(half) / 2


def available_days(user, start, end):
    """Working days a person is available in [start, end]: excludes weekly offs,
    holidays and leave; half days count 0.5."""
    logs = {l.date: l for l in _logs_between(start, end).get(user.id, [])}
    hols = holidays_between(start, end)
    total = 0.0
    d = start
    while d <= end:
        kind = day_kind(user, d, logs, hols)
        if kind in ("", DayLog.OFFICE, DayLog.WFH, DayLog.DOWNTIME):
            total += 1
        elif kind == DayLog.HALF:
            total += 0.5
        d += timedelta(days=1)
    return total


def attendance(user, month):
    a, b = month_start(month), month_end(month)
    logs = _logs_between(a, b).get(user.id, [])
    by = defaultdict(int)
    for l in logs:
        by[l.day_type] += 1
    hours = sum(l.hours for l in logs)
    job_hours = sum(sum(split_hours(l).values()) for l in logs)
    return {
        "office": by[DayLog.OFFICE], "wfh": by[DayLog.WFH], "half": by[DayLog.HALF],
        "leave": by[DayLog.LEAVE], "holiday": len([d for d in holidays_between(a, b)]),
        "downtime": by[DayLog.DOWNTIME], "late": sum(1 for l in logs if l.is_late),
        "hours": round(hours, 1), "job_hours": round(job_hours, 1),
        "logged_pct": round(job_hours / hours * 100) if hours else None,
        "leaves_counted": leave_count(user, month),
    }


# ---------------------------------------------------------------- extra work
def _extras_between(a, b):
    """({user_id: [ExtraEntry…]}, {user_id: fixed extra pay}) for a range, once per request."""
    from .perf import memo

    def load():
        entries, fixed = defaultdict(list), defaultdict(int)
        for e in ExtraEntry.objects.filter(date__range=(a, b)).select_related("project"):
            entries[e.user_id].append(e)
        for p in Project.objects.filter(is_extra=True, extra_basis="fixed", status=Project.CLOSED,
                                        closed_on__range=(a, b)).only("editor_id", "extra_rate"):
            fixed[p.editor_id] += p.extra_rate
        return entries, fixed
    return memo(("extras", a, b), load)


def extra_pay(user, month):
    entries, fixed = _extras_between(month_start(month), month_end(month))
    return sum(e.pay for e in entries.get(user.id, [])) + fixed.get(user.id, 0)


def extra_hours(user, month):
    entries, _ = _extras_between(month_start(month), month_end(month))
    return round(sum(e.hours for e in entries.get(user.id, [])), 1)


# ---------------------------------------------------------------- payroll
def payslip_figures(user, month):
    s = StudioSettings.get()
    salary = user.profile.monthly_salary
    taken = leave_count(user, month)
    unpaid = max(taken - s.free_leaves_per_month, Decimal(0))
    days = Decimal(calendar.monthrange(month.year, month.month)[1]) if s.day_pay_basis == "calendar" else Decimal(26)
    day_pay = Decimal(salary) / days if days else Decimal(0)
    deduction = int((unpaid * day_pay).quantize(Decimal("1")))
    extra = extra_pay(user, month)
    return {"salary": salary, "leaves_taken": taken, "unpaid_leaves": unpaid, "day_pay": int(day_pay.quantize(Decimal("1"))),
            "leave_deduction": deduction, "extra_pay": extra, "net": salary - deduction + extra}


# ---------------------------------------------------------------- value vs salary
def editor_value(month):
    """Invoice value earned per editor in a month (see editor_value_detail)."""
    return {uid: d["value"] for uid, d in editor_value_detail(month_start(month), month_end(month)).items()}


REVISION_STAGES = {TaskStage.REVISIONS, TaskStage.CLIENT}


def sync_film_status(p):
    """Keep a film's status in step with its events: any event in QC -> In QC; else any in TL
    review -> TL review; nothing waiting any more -> back to Revisions (one was sent back) or
    In progress. A whole film in review, or a film past QC, is left alone."""
    from .models import FilmEvent
    managed = (Project.NOT_STARTED, Project.IN_PROGRESS, Project.TL_REVISIONS, Project.REVISIONS,
               Project.TL_REVIEW, Project.QC)
    if p.status not in managed or p.whole_review:
        return
    states = set(p.events.values_list("status", flat=True))
    if FilmEvent.QC in states:
        new = Project.QC
    elif FilmEvent.TL_REVIEW in states:
        new = Project.TL_REVIEW
    elif p.status in (Project.QC, Project.TL_REVIEW):
        new = Project.REVISIONS if FilmEvent.REVISIONS in states else Project.IN_PROGRESS
    else:
        return
    if new != p.status:
        p.status = new
        p._by_events = True
        p.save(update_fields=["status"])
        p._by_events = False


def has_worked_on(p, user_id, since=None):
    """Has this person logged any time on the film (since a date)?"""
    if not user_id:
        return False
    t = TaskEntry.objects.filter(project=p, daylog__user_id=user_id)
    x = ExtraEntry.objects.filter(project=p, user_id=user_id)
    if since:
        t, x = t.filter(daylog__date__gte=since), x.filter(date__gte=since)
    return t.exists() or x.exists()


def editor_changed(p, old_id, on=None, force=False):
    """A film's editor changed. If the previous editor had already worked on it, it's a
    hand-over: they keep the days up to the day before `on` (default today), the new
    editor has it from `on`. Otherwise it's a plain replacement. Returns "handover" or None."""
    from .models import EditorSpell
    on = on or timezone.localdate()
    new_id = p.editor_id
    spells = list(p.spells.order_by("id"))
    cur = spells[-1] if spells and spells[-1].end is None else None
    handover = False
    if not spells:
        if old_id and (force or has_worked_on(p, old_id)):
            EditorSpell.objects.create(project=p, user_id=old_id, start=None, end=on - timedelta(days=1))
            handover = True
        else:
            return None                       # one editor throughout, as before
    elif cur and cur.user_id == old_id:
        if (force or has_worked_on(p, old_id, cur.start)) and not (cur.start and cur.start >= on):
            cur.end = on - timedelta(days=1)
            cur.save(update_fields=["end"])
            handover = True
        else:
            cur.delete()                      # they never started: just replaced
            spells.pop()
    if new_id:
        prev = p.spells.order_by("-id").first()
        if prev and prev.user_id == new_id and prev.end and prev.end >= on - timedelta(days=1):
            prev.end = None                   # handed straight back (e.g. undo): one spell again
            prev.save(update_fields=["end"])
        else:
            EditorSpell.objects.create(project=p, user_id=new_id, start=on)
    if p.spells.count() == 1 and p.spells.filter(user_id=new_id, end__isnull=True).exists():
        p.spells.all().delete()               # back to a single editor throughout
        return None
    return "handover" if handover else None


def editors_on(p, day, spells=None):
    """Who had the film on that day (two editors can overlap)."""
    who = [s.user_id for s in (p.spells.all() if spells is None else spells)
           if (s.start is None or s.start <= day) and (s.end is None or day <= s.end)]
    return who or ([p.editor_id] if p.editor_id else [])


def editor_on(p, day, spells=None):
    who = editors_on(p, day, spells)
    return who[0] if who else None


def current_editor(rows, today=None):
    """Of [(user_id, from, until)], who has the film now: the one whose dates hold today,
    else the next to start, else the last one."""
    today = today or timezone.localdate()
    rows = [r for r in rows if r[0]]
    if not rows:
        return None
    now = [r for r in rows if (r[1] is None or r[1] <= today) and (r[2] is None or today <= r[2])]
    if now:
        return max(now, key=lambda r: r[1] or date.min)[0]
    ahead = [r for r in rows if r[1] and r[1] > today]
    if ahead:
        return min(ahead, key=lambda r: r[1])[0]
    return max(rows, key=lambda r: r[2] or date.max)[0]


def set_editors(p, rows):
    """Set a film's editors from [(user_id, from, until)] (dates may be blank = film's start / end).
    One editor with no dates = one editor throughout (no spells)."""
    from .models import EditorSpell
    rows = [r for r in rows if r[0]]
    p.spells.all().delete()
    if len(rows) > 1 or (rows and (rows[0][1] or rows[0][2])):
        EditorSpell.objects.bulk_create([EditorSpell(project=p, user_id=u, start=a, end=b) for u, a, b in rows])
    new = current_editor(rows)
    if new != p.editor_id:
        p.editor_id = new
        p._editor_was = new                 # set here: not a hand-over
        p.save(update_fields=["editor"])


def editor_history(p):
    """[{user, start, end, current}] for a film that changed hands, else []."""
    return [{"user": s.user, "start": s.start or p.start_date, "end": s.end, "current": s.end is None}
            for s in p.spells.select_related("user__profile").order_by("id")]


def film_span(p, first_logged=None, today=None):
    """(start, end) of a film's own editing, for spreading its value over working days.
    Start = start date, else the first day anyone logged it. End = first sent to the agency,
    else the editor's deadline, else today (still running). None when it can't be placed."""
    start = p.start_date or first_logged
    end = p.first_delivered_on or p.deadline or (today or date.today())
    if not start:
        return (p.first_delivered_on, p.first_delivered_on) if p.first_delivered_on else None
    return (start, max(start, end))


def editor_value_detail(a, b):
    from .perf import memo
    return memo(("value", a, b), lambda: _editor_value_detail(a, b))


def _editor_value_detail(a, b):
    """Per editor over [a, b]: value of work and the films behind it.

    New films: a film's INR invoice is spread evenly over the working days from its start
    date to the day it was first sent to the agency (the editor's deadline until then);
    the days in [a, b] go to the film's editor.
    Revisions (hours logged as Revisions / Client Changes, or on a film after it was first
    sent) add no value - the invoice already pays for the film - unless the film has a
    revision charge, which is shared by those revision hours.
    {user_id: {"value", "days", "hours", "rhours", "films": [{"p", "days", "v", "h", "rh"}]}}"""
    from django.db.models import Min, Q
    today = timezone.localdate()
    weekdays = StudioSettings.get().weekdays
    # films whose editing may touch [a, b]
    films = {p.id: p for p in Project.objects.select_related("agency").prefetch_related("spells")
             .filter(Q(editor__isnull=False) | Q(spells__isnull=False))
             .filter(Q(start_date__lte=b) | Q(start_date__isnull=True))
             .filter(Q(first_delivered_on__gte=a) | Q(first_delivered_on__isnull=True)).distinct()}
    no_start = [pid for pid, p in films.items() if not p.start_date]
    first_log = dict(TaskEntry.objects.filter(project_id__in=no_start).values("project_id")
                     .annotate(d=Min("daylog__date")).values_list("project_id", "d")) if no_start else {}
    spans = {}
    for pid, p in films.items():
        sp = film_span(p, first_log.get(pid), today)
        if sp and sp[0] <= b and sp[1] >= a:
            spans[pid] = sp
    workdays = set()
    if spans:
        lo, hi = min(s[0] for s in spans.values()), max(s[1] for s in spans.values())
        hols = holidays_between(lo, hi)
        d = lo
        while d <= hi:
            if d.weekday() in weekdays and not (d in hols and hols[d].kind == Holiday.FULL):
                workdays.add(d)
            d += timedelta(days=1)

    out = defaultdict(lambda: {"value": 0.0, "days": 0, "hours": 0.0, "rhours": 0.0, "films": {}})

    def row(uid, p):
        return out[uid]["films"].setdefault(p.id, {"p": p, "days": 0, "v": 0.0, "h": 0.0, "rh": 0.0})

    for pid, (s0, s1) in spans.items():
        p = films[pid]
        days = [d for d in workdays if s0 <= d <= s1] or [s1]     # a film placed on an off day still counts once
        mine = [d for d in days if a <= d <= b]
        if not mine:
            continue
        per_day = p.invoice_amount / len(days) if p.agency.currency == "INR" else 0
        spells = list(p.spells.all())
        by = defaultdict(float)        # two editors: each gets their own days (shared days split)
        for d in mine:
            who = editors_on(p, d, spells)
            for uid in who:
                by[uid] += 1 / len(who)
        for uid, n in by.items():
            r = row(uid, p)
            r["days"] += round(n, 1)
            r["v"] += per_day * n
            out[uid]["days"] += round(n, 1)

    # hours logged in [a, b], split into film work and revisions
    def is_rev(stage, day, p):
        return stage in REVISION_STAGES or bool(p.first_delivered_on and day > p.first_delivered_on)

    logged = []      # (uid, pid, day, stage, hours)
    for log in DayLog.objects.filter(date__range=(a, b), tasks__isnull=False).distinct().prefetch_related("tasks"):
        sh = split_hours(log)
        for t in log.tasks.all():
            logged.append((log.user_id, t.project_id, log.date, t.stage, sh.get(t.id, 0)))
    for e in ExtraEntry.objects.filter(date__range=(a, b)).only("user_id", "project_id", "date", "start", "end", "stage"):
        logged.append((e.user_id, e.project_id, e.date, e.stage, e.hours))
    missing = {pid for _, pid, *_ in logged} - set(films)
    if missing:
        films.update(Project.objects.select_related("agency").in_bulk(missing))
    charged = {pid: p for pid, p in films.items() if p.revision_charge and p.agency.currency == "INR"}
    rev_total = {}
    if charged:      # all revision hours ever logged on films with a charge, to share it
        for pid, p in charged.items():
            tot = 0.0
            for log in DayLog.objects.filter(tasks__project_id=pid).distinct().prefetch_related("tasks"):
                sh = split_hours(log)
                tot += sum(sh.get(t.id, 0) for t in log.tasks.all() if t.project_id == pid and is_rev(t.stage, log.date, p))
            tot += sum(e.hours for e in ExtraEntry.objects.filter(project_id=pid) if is_rev(e.stage, e.date, p))
            rev_total[pid] = tot
    for uid, pid, day, stage, h in logged:
        p = films.get(pid)
        if not p or not h:
            continue
        r = row(uid, p)
        out[uid]["hours"] += h
        if is_rev(stage, day, p):
            r["rh"] += h
            out[uid]["rhours"] += h
            if pid in rev_total and rev_total[pid]:
                share = p.revision_charge * h / rev_total[pid]
                r["v"] += share
        else:
            r["h"] += h

    for d in out.values():
        fl = list(d["films"].values())
        for f in fl:
            f["v"] = int(round(f["v"]))
            f["h"], f["rh"] = round(f["h"], 1), round(f["rh"], 1)
        d["value"] = sum(f["v"] for f in fl)
        d["hours"], d["rhours"] = round(d["hours"], 1), round(d["rhours"], 1)
        d["films"] = sorted(fl, key=lambda f: (-f["v"], -f["days"], -(f["h"] + f["rh"])))
    return out


# ---------------------------------------------------------------- client-facing status
CLIENT_STAGE = {
    Project.NOT_STARTED: "Scheduled", Project.IN_PROGRESS: "Editing", Project.TL_REVIEW: "Final checks",
    Project.QC: "Final checks", Project.REVISIONS: "Changes in progress", Project.QC_PASSED: "Final checks",
    Project.TL_REVISIONS: "Editing", Project.CLIENT_REV: "Changes in progress",
    Project.SENT_AGENCY: "Delivered", Project.CLOSED: "Delivered", Project.ON_HOLD: "Scheduled",
}


def next_working_day(d, n):
    """Add n working days, skipping weekly offs and studio holidays."""
    s = StudioSettings.get()
    hols = holidays_between(d, d + timedelta(days=n * 3 + 14))
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() in s.weekdays and not (d in hols and hols[d].kind == Holiday.FULL):
            n -= 1
    return d


def client_view(project, buffer_days):
    """What the agency is told: your override, else the agency's deadline, else the
    editor's deadline plus the buffer."""
    stage = project.client_stage or CLIENT_STAGE.get(project.status, "Editing")
    when = project.client_date or project.agency_deadline or (
        next_working_day(project.deadline, buffer_days) if project.deadline else None)
    return stage, when


# ---------------------------------------------------------------- recognition
def celebrated_award(today=None):
    """The award celebrated during this month = last month's winner."""
    today = today or timezone.localdate()
    return EmployeeOfMonth.objects.filter(month=add_months(month_start(today), -1)).select_related("user", "user__profile").first()


def trophy_counts():
    out = defaultdict(int)
    for a in EmployeeOfMonth.objects.all():
        out[a.user_id] += 1
    return out


# ---------------------------------------------------------------- invoices
def next_invoice_number(currency="INR", on=None):
    """Date-based like the studio's existing invoices: DDMMYYYY, with -2, -3 for same-day extras."""
    on = on or timezone.localdate()
    base = on.strftime("%d%m%Y") + ("-US" if currency == "USD" else "")
    n, candidate = 1, base
    while Invoice.objects.filter(number=candidate).exists():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def refresh_overdue():
    today = timezone.localdate()
    Invoice.objects.filter(status__in=("sent", "part"), due_on__lt=today).update(status="overdue")


# ---------------------------------------------------------------- images
_IMAGE_LOCK = threading.Lock()


def store_image(upload, max_side=1600, quality=78):
    """Shrink an uploaded photo to a small JPEG and keep it in the database.

    Memory matters on the 512 MB host: a 40-megapixel phone photo decoded at full size
    (then rotated and converted) peaks at ~300 MB. JPEGs are decoded straight at a
    reduced scale (draft), shrunk *before* rotating, and only one photo is processed
    at a time per server process, so an upload stays around 60 MB at worst."""
    from PIL import Image, ImageOps
    with _IMAGE_LOCK:
        with Image.open(upload) as src:
            src.draft("RGB", (max_side, max_side))          # JPEG: decode at 1/2, 1/4 or 1/8 size
            src.thumbnail((max_side, max_side))
            img = ImageOps.exif_transpose(src)
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality, optimize=True)
            del img
    return StoredImage.objects.create(data=buf.getvalue())


def is_company_drive_link(url):
    """A real https Google Drive / Docs address (checked on the host name, so
    https://evil.site/?drive.google.com doesn't pass)."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit((url or "").strip())
    except ValueError:
        return False
    return u.scheme == "https" and (u.hostname or "").lower() in ("drive.google.com", "docs.google.com")


def safe_web_link(url):
    """The link if it's a plain http(s) web address, else None (no javascript:, data: etc.)."""
    from django.core.exceptions import ValidationError
    from django.core.validators import URLValidator
    url = (url or "").strip()[:500]
    try:
        URLValidator(schemes=["http", "https"])(url)
    except ValidationError:
        return None
    return url


def safe_next(request, url, fallback):
    """Only follow a 'go back to' address inside this site."""
    from django.utils.http import url_has_allowed_host_and_scheme
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return url
    return fallback


# ---------------------------------------------------------------- planner automation
def is_workday_for(user, d, logs=None, hols=None):
    """Can this person be planned on day d? (not weekly off, full holiday or leave)."""
    return day_kind(user, d, logs, hols) not in (DayLog.LEAVE, DayLog.HOLIDAY, DayLog.OFF)


def plan_fields(value):
    """Planner select value -> PlanEntry fields. 'p:12' a film, 'r:12' revisions on a delivered film, else a label."""
    value = (value or "").strip()
    if value[:2] in ("p:", "r:") and value[2:].isdigit():
        return {"project_id": int(value[2:]), "label": "Revisions" if value[:2] == "r:" else ""}
    return {"project_id": None, "label": value[:80]}


def set_plan(user, d, value, slot=1):
    """Put `value` in one slot of a planner cell ('' clears it). Slot 2 can't exist without slot 1."""
    from .models import PlanEntry
    slot = 2 if str(slot) == "2" else 1
    PlanEntry.objects.filter(user=user, date=d, slot=slot).delete()
    if value and value.strip():
        PlanEntry.objects.create(user=user, date=d, slot=slot, **plan_fields(value))
    elif slot == 1:  # clearing the main slot promotes the second one
        PlanEntry.objects.filter(user=user, date=d, slot=2).update(slot=1)


def auto_plan(project, start=None, trim=False):
    """Put a job on its editor's planner: enough working days (from its usual hours)
    between `start` (default today) and the deadline. Empty days are used first;
    if that's not enough, the job goes *alongside* other work (second slot) on busy
    days. Nothing already planned is overwritten. Days already planned for this job
    count. Planned days left on another person (after a reassignment) are removed.
    Returns (days_planned_now, days_short)."""
    import math
    from .models import PlanEntry
    today = timezone.localdate()
    if not project.pk:
        return 0, 0
    spell_users = set(project.spells.values_list("user_id", flat=True))
    (PlanEntry.objects.filter(project=project, date__gte=today).exclude(label="Revisions")
     .exclude(user_id__in={project.editor_id} | spell_users).delete())   # days left on someone else after a reassignment
    if project.is_extra or not (project.editor_id and project.deadline and project.is_open):
        return 0, 0
    spells = list(project.spells.all())
    if spells:
        return _plan_spells(project, spells)
    start = max(start or today, project.start_date or project.received_on or today, today)
    if trim:    # start or deadline moved: days planned outside the new window go
        (PlanEntry.objects.filter(project=project, user_id=project.editor_id, date__gte=today).exclude(label="Revisions")
         .filter(models.Q(date__lt=start) | models.Q(date__gt=project.deadline)).delete())
    if start > project.deadline:
        return 0, 0
    per_day = float(StudioSettings.get().hours_per_day or 8)
    left = hours_remaining(project)
    need = max(1, math.ceil(left / per_day)) if left else 1
    user = project.editor
    logs = {l.date: l for l in DayLog.objects.filter(user=user, date__range=(start, project.deadline))}
    hols = holidays_between(start, project.deadline)
    cells = defaultdict(dict)
    for e in PlanEntry.objects.filter(user=user, date__range=(start, project.deadline)):
        cells[e.date][e.slot] = e
    have = len({d for d, c in cells.items() if any(e.project_id == project.pk and not e.is_revision for e in c.values())})
    made = 0
    for wanted_slot in (1, 2):          # empty days first, then alongside other work
        d = start
        while d <= project.deadline and have + made < need:
            c = cells.get(d, {})
            mine = any(e.project_id == project.pk for e in c.values())
            if not mine and wanted_slot not in c and (wanted_slot == 1 or 1 in c) and is_workday_for(user, d, logs, hols):
                cells[d][wanted_slot] = PlanEntry.objects.create(user=user, date=d, slot=wanted_slot, project=project)
                made += 1
            d += timedelta(days=1)
    return made, max(need - have - made, 0)


def _plan_spells(project, spells):
    """A film with set editors and dates: each editor is planned on their own working days
    (from today, up to the deadline). Days planned outside someone's dates go."""
    from .models import PlanEntry
    today = timezone.localdate()
    end_all = project.deadline
    made = short = 0
    covered = set()
    for s in spells:
        a = max(s.start or project.start_date or today, today)
        b = min(s.end or end_all, end_all)
        if a > b:
            continue
        logs = {l.date: l for l in DayLog.objects.filter(user_id=s.user_id, date__range=(a, b))}
        hols = holidays_between(a, b)
        cells = defaultdict(dict)
        for e in PlanEntry.objects.filter(user_id=s.user_id, date__range=(a, b)):
            cells[e.date][e.slot] = e
        d = a
        while d <= b:
            if is_workday_for(s.user, d, logs, hols):
                covered.add((s.user_id, d))
                c = cells.get(d, {})
                if not any(e.project_id == project.pk for e in c.values()):
                    slot = 1 if 1 not in c else 2 if 2 not in c else None
                    if slot:
                        PlanEntry.objects.create(user_id=s.user_id, date=d, slot=slot, project=project)
                        made += 1
                    else:
                        short += 1
            d += timedelta(days=1)
    for e in PlanEntry.objects.filter(project=project, date__gte=today).exclude(label="Revisions"):
        if (e.user_id, e.date) not in covered:
            e.delete()
    return made, short


def drive_options():
    """Drives to pick from in the Projects footage column (once per request): the ones in the studio."""
    from . import perf
    from .models import HardDrive
    return perf.memo("drive_opts", lambda: list(HardDrive.objects.exclude(status="sent").select_related("agency")
                                                 .only("id", "code", "agency_id", "studio_owned", "agency__code",
                                                       "agency__name").order_by("code")))


def plan_mismatches(limit=40):
    """Planner days ahead where a film is planned for someone who isn't its editor on Projects
    (or the film has no editor). Revisions are left out: anyone may be given those.
    Returns [{project, user, days, first}] grouped per film and person."""
    from django.db.models import F
    from .models import PlanEntry
    today = timezone.localdate()
    qs = (PlanEntry.objects.filter(date__gte=today, project__isnull=False).exclude(label="Revisions")
          .exclude(project__status__in=(Project.CLOSED, Project.SENT_AGENCY, Project.ON_HOLD))
          .exclude(project__editor_id=F("user_id"))
          .select_related("project__agency", "project__editor__profile", "user__profile").order_by("date"))
    out = {}
    from .models import EditorSpell
    spells = defaultdict(list)
    for s in EditorSpell.objects.filter(project_id__in={e.project_id for e in qs[:500]}):
        spells[s.project_id].append(s)
    for e in qs[:500]:
        if e.user_id in editors_on(e.project, e.date, spells.get(e.project_id, [])) and spells.get(e.project_id):
            continue
        g = out.setdefault((e.project_id, e.user_id), {"project": e.project, "user": e.user, "days": 0, "first": e.date})
        g["days"] += 1
    return list(out.values())[:limit]


def plan_others(projects):
    """Tag each film with the people planned on it in the days ahead who aren't its editor."""
    from .models import PlanEntry
    projects = list(projects)
    ids = [p.pk for p in projects]
    if not ids:
        return projects
    today = timezone.localdate()
    names = defaultdict(set)
    for e in (PlanEntry.objects.filter(project_id__in=ids, date__gte=today).exclude(label="Revisions")
              .select_related("user__profile")):
        names[e.project_id].add((e.user_id, e.user.profile.name))
    from .models import EditorSpell
    set_eds = defaultdict(set)
    for pid, uid in EditorSpell.objects.filter(project_id__in=ids).values_list("project_id", "user_id"):
        set_eds[pid].add(uid)
    for p in projects:
        p.plan_others = sorted(n for uid, n in names.get(p.pk, ()) if uid != p.editor_id and uid not in set_eds[p.pk] and p.is_open
                               and p.status != Project.SENT_AGENCY)
    return projects


def repeat_plan(user, start, value, days, slot=1):
    """Copy a planner cell slot down to the next `days` working days for this person
    (skips weekly offs, holidays and leave; replaces what was in that slot)."""
    done, d, guard = 0, start, 0
    end = start + timedelta(days=days * 3 + 14)
    logs = {l.date: l for l in DayLog.objects.filter(user=user, date__range=(start, end))}
    hols = holidays_between(start, end)
    while done < days and guard < days * 3 + 14:
        d += timedelta(days=1)
        guard += 1
        if not is_workday_for(user, d, logs, hols):
            continue
        if str(slot) == "2":
            from .models import PlanEntry
            if not PlanEntry.objects.filter(user=user, date=d, slot=1).exists():
                set_plan(user, d, value, 1)   # nothing main that day: it becomes the main work
                done += 1
                continue
        set_plan(user, d, value, slot)
        done += 1
    return done, d


# ---------------------------------------------------------------- deliveries on calendars
def starts_between(a, b):
    """{date: [open films starting that day]} for the Planner."""
    qs = (Project.objects.filter(start_date__range=(a, b), editor__isnull=False)
          .exclude(status__in=(Project.CLOSED, Project.ON_HOLD, Project.SENT_AGENCY)).select_related("agency"))
    out = defaultdict(list)
    for p in qs:
        out[p.start_date].append(p)
    return out


def deliveries_between(a, b, user=None):
    """{date: [projects due that day]} (open jobs by deadline; delivered jobs on their deadline too)."""
    qs = Project.objects.filter(deadline__range=(a, b)).exclude(status=Project.ON_HOLD).select_related("agency", "editor__profile")
    if user is not None:
        qs = qs.filter(editor=user)
    out = defaultdict(list)
    for p in qs:
        out[p.deadline].append(p)
    return out


# ---------------------------------------------------------------- contract editors
def contract_earnings(user, a, b):
    """What a contract editor earned in [a, b] under their pay basis, with the line items."""
    prof = user.profile
    basis, rate = prof.contract_basis or "project", prof.contract_rate or 0
    items, total = [], 0
    if basis == "project":
        for p in Project.objects.filter(editor=user, status=Project.CLOSED, closed_on__range=(a, b)).order_by("closed_on"):
            items.append({"date": p.closed_on, "what": str(p), "qty": "1 film", "amount": p.contractor_fee, "project": p})
            total += p.contractor_fee
    elif basis in ("hour", "day"):
        for log in DayLog.objects.filter(user=user, date__range=(a, b)).order_by("date"):
            if basis == "hour":
                h = log.hours or 0
                if h:
                    amt = round(h * rate)
                    items.append({"date": log.date, "what": "Hours logged", "qty": f"{h:g} h", "amount": amt})
                    total += amt
            else:
                q = {DayLog.OFFICE: 1, DayLog.WFH: 1, DayLog.HALF: 0.5}.get(log.day_type, 0)
                if q:
                    amt = round(q * rate)
                    items.append({"date": log.date, "what": "Day worked", "qty": "½ day" if q == 0.5 else "1 day", "amount": amt})
                    total += amt
    elif basis == "month":
        m = month_start(a)
        joined = prof.joined_on
        while m <= b:
            if (not joined or joined <= month_end(m)) and m <= timezone.localdate():
                items.append({"date": m, "what": f"Retainer {m:%B %Y}", "qty": "1 month", "amount": rate})
                total += rate
            m = add_months(m, 1)
    return total, items


def contract_statement(user, month):
    """This month's earnings and payments, plus the running balance since they joined."""
    from .models import ContractPayment
    a, b = month_start(month), month_end(month)
    earned, items = contract_earnings(user, a, b)
    pays = ContractPayment.objects.filter(user=user, paid_on__range=(a, b))
    paid = sum(p.amount for p in pays)
    first = user.profile.joined_on or user.date_joined.date()
    first = min(first, a)
    all_earned, _ = contract_earnings(user, month_start(first), month_end(max(b, timezone.localdate())))
    all_paid = sum(p.amount for p in ContractPayment.objects.filter(user=user))
    missing_fees = (Project.objects.filter(editor=user, status=Project.CLOSED, contractor_fee=0)
                    if (user.profile.contract_basis or "project") == "project" else Project.objects.none())
    return {"earned": earned, "items": items, "payments": pays, "paid": paid,
            "balance": all_earned - all_paid, "all_earned": all_earned, "all_paid": all_paid, "missing_fees": missing_fees}


# ---------------------------------------------------------------- advance leave
def leave_working_days(user, start, end):
    """Working days in [start, end] for this person (weekly offs and holidays skipped)."""
    hols = holidays_between(start, end)
    weekdays = StudioSettings.get().weekdays
    d, out = start, []
    while d <= end:
        if d.weekday() in weekdays and (d not in hols or hols[d].kind != Holiday.FULL):
            out.append(d)
        d += timedelta(days=1)
    return out


def approve_leave(req, by, note=""):
    """Approve a leave request: each working day in it becomes a Leave (or Half day) entry,
    which the planner, calendars, attendance and payroll already understand. Days the person
    has already logged work on are left alone. Planned work on those days is cleared."""
    from .models import LeaveRequest, PlanEntry
    kind = DayLog.HALF if req.half_day else DayLog.LEAVE
    made = 0
    for d in leave_working_days(req.user, req.start, req.end):
        log, created = DayLog.objects.get_or_create(user=req.user, date=d, defaults={"day_type": kind,
                                                                                       "notes": f"Approved leave: {req.reason}"[:200]})
        if not created and log.tasks.exists():
            continue
        if not created:
            log.day_type = kind
            log.save(update_fields=["day_type"])
        if kind == DayLog.LEAVE:
            PlanEntry.objects.filter(user=req.user, date=d).delete()
        made += 1
    req.status, req.decided_by, req.decided_on, req.founder_note = LeaveRequest.APPROVED, by, timezone.now(), note[:200]
    req.save()
    return made


def undo_leave(req):
    """Remove the Leave entries an approval created (only untouched ones)."""
    kind = DayLog.HALF if req.half_day else DayLog.LEAVE
    DayLog.objects.filter(user=req.user, date__range=(req.start, req.end), day_type=kind, tasks__isnull=True,
                          notes__startswith="Approved leave").delete()
