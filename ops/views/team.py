"""Screens for team leads, editors and QC. Each person only ever sees their
own work (TLs: their pod). No money except their own extra pay and payslips."""
from collections import defaultdict
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from ..rbac import ADMIN, EDITOR, QC, TL, can_review_in_pod, has_perm, requires
from ..forms import CloseJobForm, DayForm, ExtraEntryForm, QuickDayForm, ReviewForm, TaskFormSet
from ..models import (QC_CHECKLIST, FilmEvent, Congrats, DayLog, EmployeeOfMonth, ExtraEntry, Holiday, Payslip, PlanEntry,
                      Project, Review, StudioSettings, TaskEntry, TaskStage)
from .. import services as S
from ..services import (add_months, attendance, celebrated_award, day_kind, extra_hours, extra_pay, holidays_between,
                        leave_count, month_end, month_start, parse_month, project_hours, split_hours, team,
                        trophy_counts)

User = get_user_model()
__all__ = ["job_event_submit", "tl_event_review", "qc_event_review", "today", "my_month", "my_day_quick", "my_jobs", "job_submit", "close_job", "my_extra", "extra_respond", "extra_delete",
           "leaderboard", "congrats", "pod", "tl_review", "qc_queue", "qc_review", "my_payslips", "payslip_view"]


def _my_projects(user):
    return Project.objects.filter(editor=user).exclude(status__in=(Project.CLOSED,)).select_related("agency", "drive")


def _day_setup(user, d):
    """The log for a day (new if none), the jobs the person may book on it, and the
    pre-filled job rows (what they logged, else what was planned)."""
    log = DayLog.objects.filter(user=user, date=d).first()
    if not log:
        kind = day_kind(user, d)
        log = DayLog(user=user, date=d, day_type=kind or DayLog.OFFICE)
    workable = Project.objects.filter(editor=user).exclude(status=Project.CLOSED) | Project.objects.filter(
        task_entries__daylog=log) if log.pk else Project.objects.filter(editor=user).exclude(status=Project.CLOSED)
    workable = workable.distinct().select_related("agency")
    initial = [{"project": t.project, "stage": t.stage, "hours": t.hours} for t in log.tasks.all()] if log.pk else []
    plans = list(PlanEntry.objects.filter(user=user, date=d).select_related("project", "project__drive__agency")
                 .prefetch_related("project__drive__photos"))
    # Revisions planned on a delivered film must be pickable in the job list too
    revision_ids = [e.project_id for e in plans if e.is_revision]
    if revision_ids:
        from django.db.models import Q
        workable = Project.objects.filter(Q(pk__in=workable.values("pk")) | Q(pk__in=revision_ids)).select_related("agency")
    if not initial:
        initial = [{"project": e.project, "stage": TaskStage.REVISIONS if e.is_revision else TaskStage.EDIT}
                   for e in plans if e.project_id]
    return log, workable, initial, plans


def _save_day(user, d, form, formset):
    with transaction.atomic():
        log = form.save(commit=False)
        log.user, log.date = user, d
        log.save()
        log.tasks.all().delete()
        for f in formset:
            c = f.cleaned_data
            if c.get("project"):
                TaskEntry.objects.create(daylog=log, project=c["project"], stage=c["stage"], hours=c.get("hours"))
                if c["project"].status == Project.NOT_STARTED:
                    Project.objects.filter(pk=c["project"].pk).update(status=Project.IN_PROGRESS)
    return log


@requires("work.log_own")
def today(request):
    user = request.user
    try:
        d = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        d = timezone.localdate()
    if d > timezone.localdate() + timedelta(days=60):
        d = timezone.localdate()
    log, workable, initial, plans = _day_setup(user, d)
    plan = plans[0] if plans else None
    while len(initial) < 3:
        initial.append({})
    form = DayForm(request.POST or None, instance=log, future=d > timezone.localdate())
    formset = TaskFormSet(request.POST or None, initial=initial, form_kwargs={"projects": workable})
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        _save_day(user, d, form, formset)
        messages.success(request, f"Saved {d:%a %d %b}.")
        return redirect(f"{request.path}?date={d.isoformat()}")
    month = month_start(d)
    ctx = {"form": form, "formset": formset, "d": d, "log": log, "plan": plan, "plans": plans,
           "prev": d - timedelta(days=1), "next": d + timedelta(days=1), "is_today": d == timezone.localdate(),
           "split": split_hours(log) if log.pk else {}, "my_jobs": _my_projects(user)[:6],
           "att": attendance(user, month), "free_leaves": StudioSettings.get().free_leaves_per_month,
           "holiday": holidays_between(d, d).get(d), "award": celebrated_award(),
           "future": d > timezone.localdate(), "deliveries": S.editor_deliveries(user, timezone.localdate()),
           "leave_req": user.leave_requests.filter(status__in=("pending", "approved"), start__lte=d, end__gte=d).first()}
    if ctx["award"]:
        ctx["congratulated"] = Congrats.objects.filter(award=ctx["award"], user=user).exists()
        ctx["congrats_list"] = ctx["award"].congrats.select_related("user__profile")
    return render(request, "team/today.html", ctx)


@requires("work.log_own")
def my_month(request):
    user = request.user
    month = parse_month(request.GET.get("m"))
    a, b = month_start(month), month_end(month)
    logs = {l.date: l for l in DayLog.objects.filter(user=user, date__range=(a, b)).prefetch_related("tasks__project")}
    plans = defaultdict(list)
    for e in PlanEntry.objects.filter(user=user, date__range=(a, b)).select_related("project"):
        plans[e.date].append(e)
    hols = holidays_between(a, b)
    due = S.deliveries_between(a, b, user=user)
    lead = (a.weekday() + 1) % 7
    cells = [None] * lead
    d = a
    today = timezone.localdate()
    while d <= b:
        cells.append(_month_cell(user, d, logs, plans, hols, due, today))
        d += timedelta(days=1)
    missing = [c["d"] for c in cells if c and c["warn"]]
    while len(cells) % 7:
        cells.append(None)
    return render(request, "team/month.html", {"cells": cells, "month": month, "prev": add_months(month, -1),
                                               "next": add_months(month, 1), "att": attendance(user, month),
                                               "free_leaves": StudioSettings.get().free_leaves_per_month,
                                               "extra_h": extra_hours(user, month), "missing": missing,
                                               "deliveries": S.editor_deliveries(user, month)})


def _month_cell(user, d, logs, plans, hols, due, today):
    log = logs.get(d)
    kind = day_kind(user, d, logs, hols)
    warn = "Not logged" if d < today and not log and kind == "" else ""
    return {"d": d, "kind": kind, "log": log, "plans": plans.get(d, []), "holiday": hols.get(d),
            "today": d == today, "warn": warn, "due": due.get(d, [])}


@requires("work.log_own")
def my_day_quick(request, day):
    """Fill a missed day from My month in a pop-up. GET draws the form, POST saves it.
    Answers fetch requests with JSON: the re-drawn calendar cell and month totals, or
    the form again with its errors."""
    from django.http import JsonResponse
    from django.template.loader import render_to_string
    user, today_ = request.user, timezone.localdate()
    try:
        d = date.fromisoformat(day)
    except ValueError:
        d = None
    if not d or d > today_:
        return JsonResponse({"ok": False, "error": "Only past days or today can be logged here."}, status=400)
    log, workable, initial, plans = _day_setup(user, d)
    if not initial:
        initial = [{}]
    form = QuickDayForm(request.POST or None, instance=log, future=False)
    formset = TaskFormSet(request.POST or None, initial=initial, form_kwargs={"projects": workable})
    saved = False
    if request.method == "POST":
        ok = form.is_valid() & formset.is_valid()          # run both, so every problem shows at once
        if formset.is_valid() and request.POST.get("day_type") in QuickDayForm.NEEDS_JOBS and not any(
                f.cleaned_data.get("project") for f in formset):
            form.add_error(None, "Add at least one job you worked on.")
            ok = False
        if ok:
            log = _save_day(user, d, form, formset)
            saved = True
    if not saved:
        html = render_to_string("team/_quick_day.html", {"form": form, "formset": formset, "d": d, "plans": plans,
                                                         "log": log}, request=request)
        if request.method == "POST" and request.headers.get("X-Requested-With") != "fetch":
            return render(request, "team/quick_day_page.html", {"inner": html, "d": d})
        if request.headers.get("X-Requested-With") == "fetch":
            return JsonResponse({"ok": False, "html": html}, status=200)
        return render(request, "team/quick_day_page.html", {"inner": html, "d": d})
    # Saved: send back the new cell and the month's numbers
    month = month_start(d)
    a, b = month, month_end(month)
    logs = {l.date: l for l in DayLog.objects.filter(user=user, date__range=(a, b)).prefetch_related("tasks__project")}
    plan_map = defaultdict(list)
    for e in PlanEntry.objects.filter(user=user, date=d).select_related("project"):
        plan_map[d].append(e)
    cell = _month_cell(user, d, logs, plan_map, holidays_between(a, b), S.deliveries_between(d, d, user=user), today_)
    label = log.get_day_type_display()
    msg = f"{d:%a %-d %b} saved: {label}" + (f", {log.hours:g} h" if log.hours else "") + "."
    if request.headers.get("X-Requested-With") != "fetch":
        messages.success(request, msg)
        return redirect(f"{reverse('my_month')}?m={d:%Y-%m}")
    hols = holidays_between(a, b)
    still = [x for x in (a + timedelta(days=i) for i in range((min(b, today_ - timedelta(days=1)) - a).days + 1))
             if x not in logs and day_kind(user, x, logs, hols) == ""]
    return JsonResponse({
        "ok": True, "message": msg, "date": d.isoformat(),
        "cell": render_to_string("team/_month_cell.html", {"c": cell}, request=request),
        "kpis": render_to_string("team/_month_kpis.html", {"att": attendance(user, month), "extra_h": extra_hours(user, month),
                                                           "free_leaves": StudioSettings.get().free_leaves_per_month}),
        "missing": [x.isoformat() for x in still]})


@requires("work.log_own")
def my_jobs(request):
    jobs = []
    for p in Project.objects.filter(editor=request.user).exclude(status=Project.CLOSED).select_related("agency", "drive__agency").prefetch_related("drive__photos", "events__reviews"):
        last = p.reviews.filter(event__isnull=True).first()
        events = list(p.events.all())
        for e in events:
            e.last = next(iter(e.reviews.all()), None)      # newest first
        jobs.append({"p": p, "hours": project_hours(p), "last": last, "delay": p.delays.first(), "events": events,
                     "by_event": events or p.deliverable in EVENT_FILMS})
    done = Project.objects.filter(editor=request.user, status=Project.CLOSED).order_by("-closed_on")[:10]
    return render(request, "team/jobs.html", {"jobs": jobs, "done": done, "event_names": FilmEvent.NAMES})


# films long enough to be sent for review one event at a time
EVENT_FILMS = ("Full Film", "Traditional Film", "Event Video")


@requires("work.log_own")
def job_event_submit(request, pk):
    """Send one event of a film (Haldi, Sangeet…) for review while the editor works on the next.
    Goes to the TL first (straight to QC for a TL), like the whole film."""
    p = get_object_or_404(Project, pk=pk, editor=request.user)
    if request.method != "POST":
        return redirect("my_jobs")
    name = " ".join(request.POST.get("event", "").split())[:40].strip()
    name = name[:1].upper() + name[1:]
    raw = request.POST.get("cut_link", "").strip()
    link = S.safe_web_link(raw)
    if not p.is_open or p.status == Project.SENT_AGENCY:
        messages.error(request, f"{p} isn't open for review.")
    elif not name:
        messages.error(request, "Pick which event you're sending (e.g. Haldi).")
    elif raw and not link:
        messages.error(request, "That review link isn't a web address. Paste the full link starting with https://")
    else:
        e = p.events.filter(name__iexact=name).first() or FilmEvent(project=p, name=name)
        if e.pk and e.status in (FilmEvent.TL_REVIEW, FilmEvent.QC):
            messages.info(request, f"{name} is already waiting for review.")
            return redirect("my_jobs")
        if e.pk and e.status == FilmEvent.PASSED:
            messages.info(request, f"{name} already passed QC.")
            return redirect("my_jobs")
        e.status = FilmEvent.QC if request.role == TL else FilmEvent.TL_REVIEW
        e.cut_link = link or e.cut_link
        e.sent_on = timezone.now()
        e.save()
        S.note_cut_link(p, e, link, request.user)
        S.sync_film_status(p)
        messages.success(request, f"{p.couple} · {name} sent for {'QC' if e.status == FilmEvent.QC else 'TL review'}. "
                                  "Carry on with the next event.")
    return redirect("my_jobs")


def _event_review(request, e, kind):
    form = ReviewForm(request.POST)
    if not form.is_valid():
        return None
    passed = request.POST.get("decision") == "pass"
    issues = [QC_CHECKLIST[int(i)] for i in request.POST.getlist("issue") if i.isdigit() and int(i) < len(QC_CHECKLIST)]
    Review.objects.create(project=e.project, event=e, kind=kind, passed=passed, issues=issues,
                          comments=form.cleaned_data["comments"], reviewer=request.user)
    if form.cleaned_data.get("cut_link") and form.cleaned_data["cut_link"] != e.cut_link:
        e.cut_link = form.cleaned_data["cut_link"]
        S.note_cut_link(e.project, e, e.cut_link, request.user)
    if kind == Review.TL:
        e.status = FilmEvent.QC if passed else FilmEvent.REVISIONS
    else:
        e.status = FilmEvent.PASSED if passed else FilmEvent.REVISIONS
    e.save(update_fields=["status", "cut_link", "updated"])
    S.sync_film_status(e.project)
    return passed


@requires("pod.review")
def tl_event_review(request, pk):
    e = get_object_or_404(FilmEvent.objects.select_related("project"), pk=pk, status=FilmEvent.TL_REVIEW)
    if not can_review_in_pod(request.user, e.project):
        raise PermissionDenied
    if request.method == "POST":
        passed = _event_review(request, e, Review.TL)
        if passed is not None:
            messages.success(request, f"{e}: " + ("sent to QC." if passed else "sent back with notes."))
    return redirect("pod")


@requires("qc.review")
def qc_event_review(request, pk):
    e = get_object_or_404(FilmEvent.objects.select_related("project__editor__profile"), pk=pk, status=FilmEvent.QC)
    if request.method == "POST":
        passed = _event_review(request, e, Review.QC)
        if passed is not None:
            who = e.project.editor.profile.name if e.project.editor_id else "the editor"
            messages.success(request, f"{e}: " + ("passed." if passed else f"sent back to {who}."))
    return redirect(f"{reverse('qc_queue')}?p={e.project_id}")


@requires("work.log_own")
def job_submit(request, pk):
    """Editor says the cut is ready → goes to their TL (or straight to QC if they are a TL)."""
    p = get_object_or_404(Project, pk=pk, editor=request.user)
    if request.method == "POST" and p.can_submit:
        raw = request.POST.get("cut_link", "").strip()
        link = S.safe_web_link(raw)
        if raw and not link:
            messages.error(request, "That review link isn't a web address. Paste the full link starting with https://")
            return redirect("my_jobs")
        if link:
            p.cut_link = link
            S.note_cut_link(p, None, link, request.user)
        p.status = Project.QC if request.role == TL else Project.TL_REVIEW
        p._status_was = None            # the whole film is sent now (even if events had it in review)
        p.save(update_fields=["cut_link", "status"])
        messages.success(request, "Sent for " + ("QC." if p.status == Project.QC else "TL review."))
    return redirect("my_jobs")


@requires("work.log_own")
def close_job(request, pk):
    """The editor uploads the final files and closes the job, at any stage: closing
    marks the film Delivered (QC hasn't always been recorded in the portal)."""
    p = get_object_or_404(Project.objects.exclude(status=Project.CLOSED), pk=pk, editor=request.user)
    form = CloseJobForm(request.POST or None, initial={"project_file_link": p.project_file_link,
                                                       "final_mp4_link": p.final_mp4_link})
    qc_ok = p.status in (Project.QC_PASSED, Project.SENT_AGENCY)
    if request.method == "POST" and form.is_valid():
        before = p.get_status_display()
        p.project_file_link = form.cleaned_data["project_file_link"]
        p.final_mp4_link = form.cleaned_data["final_mp4_link"]
        p.status = Project.CLOSED      # first delivery date is kept if this was a client revision
        p.save()
        from ..audit import record
        record(request, "project.closed", str(p), f"by editor from {before}" + ("" if qc_ok else " (QC not passed in the portal)"))
        if p.drive and not p.drive.open_jobs.exists() and p.drive.status == "in_use":
            p.drive.status = "backup_done" if p.drive.studio_owned else "ready"
            p.drive.save(update_fields=["status"])
        messages.success(request, f"{p} closed. Nice work!")
        return redirect("my_jobs")
    return render(request, "team/close_job.html", {"p": p, "form": form, "qc_ok": qc_ok})


@requires("extra.log_own")
def my_extra(request):
    user = request.user
    month = parse_month(request.GET.get("m"))
    form = ExtraEntryForm(request.POST or None, user=user, initial={"date": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        e = form.save(commit=False)
        e.user = user
        e.save()
        msg = f"Logged {e.hours} extra hrs."
        if e.overlaps_office:
            msg += " These overlap your office time, so admin will see a note."
        messages.success(request, msg)
        return redirect("my_extra")
    a, b = month_start(month), month_end(month)
    # No accepting needed: every extra job given to them, and any film they logged extra hours on
    jobs = list(Project.objects.filter(Q(editor=user, is_extra=True) | Q(extra_entries__user=user))
                .exclude(extra_accepted=False).distinct().select_related("agency")
                .defer("brief", "agency__billing_address", "agency__notes").order_by("status", "-pk"))
    coming = [p for p in jobs if p.extra_basis == "fixed" and p.extra_rate and not S.extra_paid_on(p)]
    for p in jobs:
        p.x_paid_in = S.extra_paid_on(p)
    return render(request, "team/extra.html", {
        "form": form, "month": month, "prev": add_months(month, -1), "next": add_months(month, 1),
        "jobs": jobs, "coming": coming, "coming_total": sum(p.extra_rate for p in coming),
        "entries": ExtraEntry.objects.filter(user=user, date__range=(a, b)).select_related("project"),
        "pay": extra_pay(user, month), "hours": extra_hours(user, month)})


@requires("extra.log_own")
def extra_respond(request, pk):
    p = get_object_or_404(Project, pk=pk, editor=request.user, is_extra=True)
    if request.method == "POST":
        p.extra_accepted = request.POST.get("answer") == "yes"
        if not p.extra_accepted:
            p.editor = None  # goes back to the 'needs an editor' queue
        p.save(update_fields=["extra_accepted", "editor"])
        messages.success(request, "Accepted. Log your hours here as you go." if p.extra_accepted else "Declined.")
    return redirect("my_extra")


@requires("extra.log_own")
def extra_delete(request, pk):
    e = get_object_or_404(ExtraEntry, pk=pk, user=request.user)
    if request.method == "POST":
        e.delete()
        messages.success(request, "Entry removed.")
    return redirect("my_extra")


@requires("leaderboard.view")
def leaderboard(request):
    counts = trophy_counts()
    people = list(User.objects.filter(profile__active=True).exclude(profile__role=ADMIN).select_related("profile"))
    last_win, won = {}, defaultdict(list)
    for a in EmployeeOfMonth.objects.all():
        last_win.setdefault(a.user_id, a.month)
        won[a.user_id].append(a.month)
    people.sort(key=lambda u: (-counts.get(u.id, 0), -(last_win.get(u.id, date.min).toordinal())))
    rows = [{"u": u, "count": counts.get(u.id, 0), "cups": range(counts.get(u.id, 0)),
             "months": won.get(u.id, [])} for u in people]
    podium = [r for r in rows if r["count"]][:3]
    nxt = add_months(month_start(timezone.localdate()), 1)
    return render(request, "team/leaderboard.html", {"rows": rows, "podium": podium, "next_announce": nxt,
                                                     "days_to": (nxt - timezone.localdate()).days,
                                                     "recent": EmployeeOfMonth.objects.select_related("user__profile")[:6]})


@requires("leaderboard.view")
def congrats(request):
    award = celebrated_award()
    if request.method == "POST" and award and award.user_id != request.user.id:
        Congrats.objects.get_or_create(award=award, user=request.user,
                                       defaults={"message": request.POST.get("message", "")[:200]})
        messages.success(request, f"Congrats sent to {award.user.profile.name}!")
    return redirect(S.safe_next(request, request.POST.get("next"), reverse("home")))


@requires("pod.review")
def pod(request):
    my_pod = getattr(request.user.profile, "pod", None)
    members = list(team().filter(profile__pod=my_pod)) if my_pod else [request.user]
    today_ = timezone.localdate()
    logs = {l.user_id: l for l in DayLog.objects.filter(user__in=members, date=today_).prefetch_related("tasks__project")}
    jobs = Project.objects.filter(editor__in=members).exclude(status=Project.CLOSED).select_related("editor__profile", "agency")
    waiting = jobs.filter(status=Project.TL_REVIEW, whole_review=True)
    ev_waiting = defaultdict(list)
    for e in FilmEvent.objects.filter(project__in=jobs, status=FilmEvent.TL_REVIEW).select_related("project__agency",
                                                                                                  "project__editor__profile"):
        ev_waiting[e.project].append(e)
    return render(request, "team/pod.html", {"pod": my_pod, "members": [{"u": u, "log": logs.get(u.id)} for u in members],
                                             "jobs": jobs, "waiting": waiting, "form": ReviewForm(),
                                             "ev_waiting": [{"p": p, "events": es} for p, es in ev_waiting.items()]})


def _review(request, p, kind):
    form = ReviewForm(request.POST)
    # (whole film; one event at a time goes through _event_review)
    if not form.is_valid():
        return False
    passed = request.POST.get("decision") == "pass"
    issues = [QC_CHECKLIST[int(i)] for i in request.POST.getlist("issue") if i.isdigit() and int(i) < len(QC_CHECKLIST)]
    Review.objects.create(project=p, kind=kind, passed=passed, issues=issues,
                          comments=form.cleaned_data["comments"], reviewer=request.user)
    if form.cleaned_data.get("cut_link") and form.cleaned_data["cut_link"] != p.cut_link:
        p.cut_link = form.cleaned_data["cut_link"]
        S.note_cut_link(p, None, p.cut_link, request.user)
    if kind == Review.TL:
        p.status = Project.QC if passed else Project.TL_REVISIONS
    else:
        p.status = Project.QC_PASSED if passed else Project.REVISIONS
    p.save(update_fields=["status", "cut_link"])
    return passed


@requires("pod.review")
def tl_review(request, pk):
    p = get_object_or_404(Project, pk=pk, status=Project.TL_REVIEW, whole_review=True)
    if not can_review_in_pod(request.user, p):
        from ..audit import record
        record(request, "access.denied", f"project {p.pk}", "TL review outside own pod")
        raise PermissionDenied
    if request.method == "POST":
        passed = _review(request, p, Review.TL)
        messages.success(request, f"{p}: " + ("sent to QC." if passed else "sent back with notes."))
    return redirect("pod")


@requires("qc.review")
def qc_queue(request):
    from django.db.models import Count, Prefetch
    queue = (Project.objects.filter(Q(status=Project.QC, whole_review=True) | Q(events__status=FilmEvent.QC)).distinct()
             .select_related("agency", "editor__profile", "drive")
             .annotate(ev_waiting=Count("events", filter=Q(events__status=FilmEvent.QC), distinct=True))
             .order_by("deadline"))
    sel = queue.filter(pk=request.GET.get("p")).first() or queue.first()
    events = []
    if sel:
        events = list(sel.events.prefetch_related(Prefetch("reviews", queryset=Review.objects.select_related("reviewer__profile"))))
        open_one = next((e for e in events if e.status == FilmEvent.QC), None)
        for e in events:
            e.is_open = e is open_one
    ac = _autocheck_units(sel, events) if sel else {}
    passed_m = Review.objects.filter(kind="qc", created__date__gte=month_start(timezone.localdate()))
    first_time = None
    if passed_m.exists():
        projs = {r.project_id for r in passed_m}
        ok = sum(1 for pid in projs if not Review.objects.filter(project_id=pid, kind="qc", passed=False).exists())
        first_time = round(ok / len(projs) * 100)
    board, counts = qc_board()
    show = request.GET.get("s", "")
    if show == "closed":
        board = [b for b in board if b["p"].qc_closed_on]
    elif show:
        board = [b for b in board if b["state"] == show or (show == "failed" and b["fails"])]
    founder = has_perm(request.user, "payroll.manage")
    from .qcpay import qc_month_summary
    from ..rbac import QC as QC_ROLE
    month = month_start(timezone.localdate())
    return render(request, "team/qc.html", {
        "queue": queue, "sel": sel, "checklist": list(enumerate(QC_CHECKLIST)), "form": ReviewForm(),
        "history": sel.reviews.filter(event__isnull=True).select_related("reviewer__profile") if sel else [],
        "events": events, "whole_in_qc": bool(sel and sel.status == Project.QC and sel.whole_review),
        "whole_ac": ac.get(None),
        "helper_version": __import__("ops.views.autocheck", fromlist=["helper_version"]).helper_version(),
        "founder": founder, "qc_summary": qc_month_summary(month) if founder else [], "month": month,
        "sendable": list(Project.objects.filter(Q(status__in=Project.WORKABLE) | Q(status=Project.TL_REVIEW)
                                               | Q(status=Project.QC, whole_review=False)).select_related("editor__profile", "agency")
                         .order_by("couple", "deliverable")) if founder else [],
        "event_names": FilmEvent.NAMES,
        "qc_people": list(get_user_model().objects.filter(Q(profile__role=QC_ROLE) | Q(pk=request.user.pk), is_active=True)
                          .select_related("profile")) if founder else [],
        "board": board, "counts": counts, "show": show, "states": QC_STATES,
        "first_time": first_time})


def _autocheck_units(p, events):
    """AutoCheck per event (and for the whole film, key None): the two versions to compare,
    taken from the links the editor sent, and the latest checks. Sets e.ac on each event."""
    from ..models import AutoCheckRun, CutLink
    from .autocheck import labels
    links, runs = defaultdict(list), defaultdict(list)
    for c in CutLink.objects.filter(project=p).order_by("-round", "-sent_at"):
        links[c.event_id].append(c)
    for r in AutoCheckRun.objects.filter(project=p).select_related("started_by__profile").order_by("-started_at")[:40]:
        runs[r.event_id].append(r)
    units = {}
    for key, cut in [(None, p.cut_link)] + [(e.pk, e.cut_link) for e in events]:
        ls = links[key]
        last = ls[0] if ls else None
        prev = ls[1] if len(ls) > 1 else None
        rs = runs[key][:3]
        u = {"last": last, "prev": prev, "link": last.link if last else cut, "runs": rs, "run": rs[0] if rs else None}
        if last:
            u["cur_label"], u["prev_label"] = f"V{last.round}", (f"V{prev.round}" if prev else "")
        else:
            u["cur_label"], u["prev_label"] = "V1", ""
        u["frameio"] = bool(S.frameio_link(u["link"]))
        u["prev_frameio"] = bool(prev and S.frameio_link(prev.link))
        units[key] = u
    for e in events:
        e.ac = units[e.pk]
        last = e.ac["last"]
        # sent again with the same link: a new version stacked on it in Frame.io can't be compared
        e.ac["same"] = bool(last and e.sent_on and e.sent_on > last.sent_at + timedelta(minutes=1))
    return units


# key, label, tag colour
QC_STATES = [("waiting", "Waiting for QC", "qc"), ("recheck", "Back for re-check", "warn"),
             ("sent_back", "Sent back", "bad"), ("passed", "Passed", "ok"), ("delivered", "Delivered", "info")]


def qc_board(days=120):
    """Every film that reached QC recently, with where it stands now: waiting, back for a
    re-check after a fail, sent back to the editor, passed, or delivered."""
    from django.db.models import Prefetch, Q
    since = timezone.now() - timedelta(days=days)
    qs = (Project.objects.filter(Q(status=Project.QC) | Q(events__status=FilmEvent.QC)
                                 | Q(reviews__kind=Review.QC, reviews__created__gte=since)).distinct()
          .select_related("agency", "editor__profile", "qc_closed_by__profile").prefetch_related("events")
          .prefetch_related(Prefetch("reviews", queryset=Review.objects.filter(kind=Review.QC).select_related("reviewer__profile", "event"),
                                     to_attr="qc_reviews")))
    label = {k: (lbl, cls) for k, lbl, cls in QC_STATES}
    rows = []
    for p in qs:
        revs = p.qc_reviews          # newest first
        last = revs[0] if revs else None
        fails = sum(1 for r in revs if not r.passed)
        evs = list(p.events.all())
        if p.status == Project.QC or any(e.status == FilmEvent.QC for e in evs):
            state = "recheck" if fails else "waiting"
        elif last and not last.passed:
            state = "sent_back"
        elif p.status in (Project.CLOSED, Project.CLIENT_REV) or p.first_delivered_on:
            state = "delivered"
        else:
            state = "passed"
        rows.append({"p": p, "events": evs, "state": state, "label": label[state][0], "cls": label[state][1], "last": last,
                     "rounds": len(revs), "fails": fails, "first_time": bool(revs) and fails == 0 and last.passed,
                     "when": last.created if last else None})
    order = {k: i for i, (k, _, _) in enumerate(QC_STATES)}
    rows.sort(key=lambda b: (order[b["state"]], -(b["when"].timestamp() if b["when"] else 0)))
    counts = {k: sum(1 for b in rows if b["state"] == k) for k, _, _ in QC_STATES}
    counts["failed"] = sum(1 for b in rows if b["fails"])
    counts["closed"] = sum(1 for b in rows if b["p"].qc_closed_on)
    counts["all"] = len(rows)
    return rows, counts


@requires("qc.review")
def qc_review(request, pk):
    p = get_object_or_404(Project, pk=pk, status=Project.QC, whole_review=True)
    if request.method == "POST":
        passed = _review(request, p, Review.QC)
        messages.success(request, f"{p}: " + ("passed, ready to deliver." if passed else f"sent back to {p.editor.profile.name if p.editor else 'the editor'}."))
    return redirect("qc_queue")


@requires("payslips.view_own")
def my_payslips(request):
    slips = Payslip.objects.filter(user=request.user, payroll__finalized=True).select_related("payroll")
    return render(request, "team/payslips.html", {"slips": slips})


@login_required
def payslip_view(request, pk):
    slip = get_object_or_404(Payslip.objects.select_related("payroll", "user__profile"), pk=pk)
    own = slip.user_id == request.user.id and slip.payroll.finalized and has_perm(request.user, "payslips.view_own")
    if not (own or has_perm(request.user, "payroll.manage")):
        raise PermissionDenied
    return render(request, "print/payslip.html", {"s": slip})
