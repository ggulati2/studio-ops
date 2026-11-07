"""Business rules. Views call these; nothing here touches the request."""
import calendar
import io
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from .models import (DayLog, EmployeeOfMonth, ExtraEntry, Holiday, Invoice, Profile, Project,
                     StoredImage, StudioSettings, TaskEntry)

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


def project_hours(project):
    total = 0.0
    for log in DayLog.objects.filter(tasks__project=project).distinct().prefetch_related("tasks"):
        sh = split_hours(log)
        total += sum(h for t_id, h in sh.items()
                     if next(t for t in log.tasks.all() if t.id == t_id).project_id == project.id)
    total += sum(e.hours for e in project.extra_entries.all())
    return round(total, 1)


def benchmarks():
    """Average hours of delivered films by (deliverable, tier)."""
    sums = defaultdict(lambda: [0.0, 0])
    for p in Project.objects.filter(status=Project.CLOSED):
        h = project_hours(p)
        if h:
            sums[(p.deliverable, p.tier)][0] += h
            sums[(p.deliverable, p.tier)][1] += 1
    return {k: round(v[0] / v[1], 1) for k, v in sums.items()}


# Starting estimates (hours, Tier 2) until the studio has its own delivered films
# for a deliverable/tier. Tier 1 ×0.7, Tier 3 ×1.4. Your real averages replace these.
DEFAULT_HOURS = {"Teaser": 10, "Highlight": 24, "Trailer": 20, "Full Film": 30, "Traditional Film": 22, "Reels": 3,
                 "Same Day Edit": 8, "Event Video": 16, "Ad / Corporate": 20, "Cleanup Shots": 4, "Other": 8}
TIER_FACTOR = {1: 0.7, 2: 1.0, 3: 1.4}


def usual_hours(project, bench=None):
    bench = bench if bench is not None else benchmarks()
    return bench.get((project.deliverable, project.tier)) or round(
        DEFAULT_HOURS.get(project.deliverable, 8) * TIER_FACTOR.get(project.tier, 1), 1)


def hours_remaining(project, bench=None):
    usual = usual_hours(project, bench)
    if not usual or not project.is_open:
        return 0.0
    return max(usual - project_hours(project), 0.0)


# ---------------------------------------------------------------- calendar / leave
def holidays_between(a, b):
    return {h.date: h for h in Holiday.objects.filter(date__range=(a, b))}


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
    qs = DayLog.objects.filter(user=user, date__range=(a, b))
    full = qs.filter(day_type=DayLog.LEAVE).count()
    hols = holidays_between(a, b)
    half = sum(1 for l in qs.filter(day_type=DayLog.HALF) if not (l.date in hols and hols[l.date].kind == Holiday.HALF))
    return Decimal(full) + Decimal(half) / 2


def available_days(user, start, end):
    """Working days a person is available in [start, end]: excludes weekly offs,
    holidays and leave; half days count 0.5."""
    s = StudioSettings.get()
    logs = {l.date: l for l in DayLog.objects.filter(user=user, date__range=(start, end))}
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
    logs = list(DayLog.objects.filter(user=user, date__range=(a, b)).prefetch_related("tasks"))
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
def extra_pay(user, month):
    a, b = month_start(month), month_end(month)
    hourly = sum(e.pay for e in ExtraEntry.objects.filter(user=user, date__range=(a, b)).select_related("project"))
    fixed = sum(p.extra_rate for p in Project.objects.filter(
        editor=user, is_extra=True, extra_basis="fixed", status=Project.CLOSED, closed_on__range=(a, b)))
    return hourly + fixed


def extra_hours(user, month):
    a, b = month_start(month), month_end(month)
    return round(sum(e.hours for e in ExtraEntry.objects.filter(user=user, date__range=(a, b))), 1)


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
    """Invoice value earned per editor in a month: each film's invoice is shared
    by hours; a film's rate = invoice ÷ max(actual hours, usual hours)."""
    a, b = month_start(month), month_end(month)
    bench = benchmarks()
    rate_cache = {}
    value = defaultdict(float)
    hrs = task_hours(DayLog.objects.filter(date__range=(a, b)))
    for (uid, pid), h in hrs.items():
        if pid not in rate_cache:
            p = Project.objects.get(pk=pid)
            denom = max(project_hours(p), bench.get((p.deliverable, p.tier), 0))
            rate_cache[pid] = (p.invoice_amount / denom) if (denom and p.agency.currency == "INR") else 0
        value[uid] += h * rate_cache[pid]
    return {k: int(v) for k, v in value.items()}


# ---------------------------------------------------------------- client-facing status
CLIENT_STAGE = {
    Project.NOT_STARTED: "Scheduled", Project.IN_PROGRESS: "Editing", Project.TL_REVIEW: "Final checks",
    Project.QC: "Final checks", Project.REVISIONS: "Changes in progress", Project.QC_PASSED: "Final checks",
    Project.TL_REVISIONS: "Editing", Project.CLIENT_REV: "Changes in progress",
    Project.CLOSED: "Delivered", Project.ON_HOLD: "Scheduled",
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
def store_image(upload, max_side=1600, quality=78):
    """Shrink an uploaded photo to a small JPEG and keep it in the database."""
    from PIL import Image, ImageOps
    img = Image.open(upload)
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return StoredImage.objects.create(data=buf.getvalue())


def is_company_drive_link(url):
    return bool(url) and ("drive.google.com" in url or "docs.google.com" in url)


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


def auto_plan(project, start=None):
    """Put a job on its editor's planner: enough working days (from its usual hours)
    between `start` (default today) and the deadline. Empty days are used first;
    if that's not enough, the job goes *alongside* other work (second slot) on busy
    days. Nothing already planned is overwritten. Days already planned for this job
    count. Planned days left on another person (after a reassignment) are removed.
    Returns (days_planned_now, days_short)."""
    import math
    from .models import PlanEntry
    today = timezone.localdate()
    if not project.pk or project.is_extra:
        return 0, 0
    PlanEntry.objects.filter(project=project, date__gte=today).exclude(user_id=project.editor_id).delete()
    if not (project.editor_id and project.deadline and project.is_open):
        return 0, 0
    start = max(start or today, project.received_on or today, today)
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
