"""Screens for team leads, editors and QC. Each person only ever sees their
own work (TLs: their pod). No money except their own extra pay and payslips."""
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..access import ADMIN, EDITING, EDITOR, EVERYONE, QC, TL, roles
from ..forms import CloseJobForm, DayForm, ExtraEntryForm, ReviewForm, TaskFormSet
from ..models import (QC_CHECKLIST, Congrats, DayLog, EmployeeOfMonth, ExtraEntry, Holiday, Payslip, PlanEntry,
                      Project, Review, StudioSettings, TaskEntry)
from ..services import (add_months, attendance, celebrated_award, day_kind, extra_hours, extra_pay, holidays_between,
                        leave_count, month_end, month_start, parse_month, project_hours, split_hours, team,
                        trophy_counts)

User = get_user_model()
__all__ = ["today", "my_month", "my_jobs", "job_submit", "close_job", "my_extra", "extra_respond", "extra_delete",
           "leaderboard", "congrats", "pod", "tl_review", "qc_queue", "qc_review", "my_payslips", "payslip_view"]


def _my_projects(user):
    return Project.objects.filter(editor=user).exclude(status__in=(Project.CLOSED,)).select_related("agency", "drive")


@roles(*EDITING)
def today(request):
    user = request.user
    try:
        d = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        d = timezone.localdate()
    if d > timezone.localdate() + timedelta(days=60):
        d = timezone.localdate()
    log = DayLog.objects.filter(user=user, date=d).first()
    if not log:
        kind = day_kind(user, d)
        log = DayLog(user=user, date=d, day_type=kind or DayLog.OFFICE)
    workable = Project.objects.filter(editor=user).exclude(status=Project.CLOSED) | Project.objects.filter(
        task_entries__daylog=log) if log.pk else Project.objects.filter(editor=user).exclude(status=Project.CLOSED)
    workable = workable.distinct().select_related("agency")
    initial = [{"project": t.project, "stage": t.stage, "hours": t.hours} for t in log.tasks.all()] if log.pk else []
    plan = PlanEntry.objects.filter(user=user, date=d).select_related("project", "project__drive").first()
    if not initial and plan and plan.project_id:
        initial = [{"project": plan.project, "stage": "Edit"}]
    while len(initial) < 3:
        initial.append({})
    form = DayForm(request.POST or None, instance=log)
    formset = TaskFormSet(request.POST or None, initial=initial, form_kwargs={"projects": workable})
    if request.method == "POST" and form.is_valid() and formset.is_valid():
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
        messages.success(request, f"Saved {d:%a %d %b}.")
        return redirect(f"{request.path}?date={d.isoformat()}")
    month = month_start(d)
    ctx = {"form": form, "formset": formset, "d": d, "log": log, "plan": plan,
           "prev": d - timedelta(days=1), "next": d + timedelta(days=1), "is_today": d == timezone.localdate(),
           "split": split_hours(log) if log.pk else {}, "my_jobs": _my_projects(user)[:6],
           "att": attendance(user, month), "free_leaves": StudioSettings.get().free_leaves_per_month,
           "holiday": holidays_between(d, d).get(d), "award": celebrated_award()}
    if ctx["award"]:
        ctx["congratulated"] = Congrats.objects.filter(award=ctx["award"], user=user).exists()
        ctx["congrats_list"] = ctx["award"].congrats.select_related("user__profile")
    return render(request, "team/today.html", ctx)


@roles(*EDITING)
def my_month(request):
    user = request.user
    month = parse_month(request.GET.get("m"))
    a, b = month_start(month), month_end(month)
    logs = {l.date: l for l in DayLog.objects.filter(user=user, date__range=(a, b)).prefetch_related("tasks__project")}
    plans = {p.date: p for p in PlanEntry.objects.filter(user=user, date__range=(a, b)).select_related("project")}
    hols = holidays_between(a, b)
    lead = (a.weekday() + 1) % 7
    cells = [None] * lead
    d = a
    today = timezone.localdate()
    while d <= b:
        log = logs.get(d)
        kind = day_kind(user, d, logs, hols)
        warn = ""
        if d < today and not log and kind == "":
            warn = "Not logged"
        cells.append({"d": d, "kind": kind, "log": log, "plan": plans.get(d), "holiday": hols.get(d),
                      "today": d == today, "warn": warn})
        d += timedelta(days=1)
    while len(cells) % 7:
        cells.append(None)
    return render(request, "team/month.html", {"cells": cells, "month": month, "prev": add_months(month, -1),
                                               "next": add_months(month, 1), "att": attendance(user, month),
                                               "free_leaves": StudioSettings.get().free_leaves_per_month,
                                               "extra_h": extra_hours(user, month)})


@roles(*EDITING)
def my_jobs(request):
    jobs = []
    for p in Project.objects.filter(editor=request.user).exclude(status=Project.CLOSED).select_related("agency", "drive"):
        last = p.reviews.first()
        jobs.append({"p": p, "hours": project_hours(p), "last": last})
    done = Project.objects.filter(editor=request.user, status=Project.CLOSED).order_by("-closed_on")[:10]
    return render(request, "team/jobs.html", {"jobs": jobs, "done": done})


@roles(*EDITING)
def job_submit(request, pk):
    """Editor says the cut is ready → goes to their TL (or straight to QC if they are a TL)."""
    p = get_object_or_404(Project, pk=pk, editor=request.user)
    if request.method == "POST":
        link = request.POST.get("cut_link", "").strip()
        if link:
            p.cut_link = link
        p.status = Project.QC if request.role == TL else Project.TL_REVIEW
        p.save(update_fields=["cut_link", "status"])
        messages.success(request, "Sent for " + ("QC." if p.status == Project.QC else "TL review."))
    return redirect("my_jobs")


@roles(*EDITING)
def close_job(request, pk):
    p = get_object_or_404(Project, pk=pk, editor=request.user)
    form = CloseJobForm(request.POST or None, initial={"project_file_link": p.project_file_link,
                                                       "final_mp4_link": p.final_mp4_link})
    qc_ok = p.status == Project.QC_PASSED
    if request.method == "POST" and qc_ok and form.is_valid():
        p.project_file_link = form.cleaned_data["project_file_link"]
        p.final_mp4_link = form.cleaned_data["final_mp4_link"]
        p.status, p.closed_on = Project.CLOSED, timezone.localdate()
        p.save()
        if p.drive and not p.drive.open_jobs.exists() and p.drive.status == "in_use":
            p.drive.status = "backup_done" if p.drive.studio_owned else "ready"
            p.drive.save(update_fields=["status"])
        messages.success(request, f"{p} closed. Nice work!")
        return redirect("my_jobs")
    return render(request, "team/close_job.html", {"p": p, "form": form, "qc_ok": qc_ok})


@roles(*EDITING)
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
    return render(request, "team/extra.html", {
        "form": form, "month": month, "prev": add_months(month, -1), "next": add_months(month, 1),
        "offers": Project.objects.filter(editor=user, is_extra=True, extra_accepted__isnull=True),
        "jobs": Project.objects.filter(editor=user, is_extra=True, extra_accepted=True).select_related("agency"),
        "entries": ExtraEntry.objects.filter(user=user, date__range=(a, b)).select_related("project"),
        "pay": extra_pay(user, month), "hours": extra_hours(user, month)})


@roles(*EDITING)
def extra_respond(request, pk):
    p = get_object_or_404(Project, pk=pk, editor=request.user, is_extra=True)
    if request.method == "POST":
        p.extra_accepted = request.POST.get("answer") == "yes"
        if not p.extra_accepted:
            p.editor = None  # goes back to the 'needs an editor' queue
        p.save(update_fields=["extra_accepted", "editor"])
        messages.success(request, "Accepted. Log your hours here as you go." if p.extra_accepted else "Declined.")
    return redirect("my_extra")


@roles(*EDITING)
def extra_delete(request, pk):
    e = get_object_or_404(ExtraEntry, pk=pk, user=request.user)
    if request.method == "POST":
        e.delete()
        messages.success(request, "Entry removed.")
    return redirect("my_extra")


@roles(*EVERYONE)
def leaderboard(request):
    counts = trophy_counts()
    people = list(User.objects.filter(profile__active=True).exclude(profile__role=ADMIN).select_related("profile"))
    last_win = {}
    for a in EmployeeOfMonth.objects.all():
        last_win.setdefault(a.user_id, a.month)
    people.sort(key=lambda u: (-counts.get(u.id, 0), -(last_win.get(u.id, date.min).toordinal())))
    rows = [{"u": u, "count": counts.get(u.id, 0), "cups": range(counts.get(u.id, 0)),
             "months": [a.month for a in EmployeeOfMonth.objects.filter(user=u)]} for u in people]
    podium = [r for r in rows if r["count"]][:3]
    nxt = add_months(month_start(timezone.localdate()), 1)
    return render(request, "team/leaderboard.html", {"rows": rows, "podium": podium, "next_announce": nxt,
                                                     "days_to": (nxt - timezone.localdate()).days,
                                                     "recent": EmployeeOfMonth.objects.select_related("user__profile")[:6]})


@roles(*EVERYONE)
def congrats(request):
    award = celebrated_award()
    if request.method == "POST" and award and award.user_id != request.user.id:
        Congrats.objects.get_or_create(award=award, user=request.user,
                                       defaults={"message": request.POST.get("message", "")[:200]})
        messages.success(request, f"Congrats sent to {award.user.profile.name}!")
    return redirect(request.POST.get("next") or "home")


@roles(TL)
def pod(request):
    my_pod = getattr(request.user.profile, "pod", None)
    members = list(team().filter(profile__pod=my_pod)) if my_pod else [request.user]
    today_ = timezone.localdate()
    logs = {l.user_id: l for l in DayLog.objects.filter(user__in=members, date=today_).prefetch_related("tasks__project")}
    jobs = Project.objects.filter(editor__in=members).exclude(status=Project.CLOSED).select_related("editor__profile", "agency")
    waiting = jobs.filter(status=Project.TL_REVIEW)
    return render(request, "team/pod.html", {"pod": my_pod, "members": [{"u": u, "log": logs.get(u.id)} for u in members],
                                             "jobs": jobs, "waiting": waiting, "form": ReviewForm()})


def _review(request, p, kind):
    form = ReviewForm(request.POST)
    if not form.is_valid():
        return False
    passed = request.POST.get("decision") == "pass"
    issues = [QC_CHECKLIST[int(i)] for i in request.POST.getlist("issue") if i.isdigit() and int(i) < len(QC_CHECKLIST)]
    Review.objects.create(project=p, kind=kind, passed=passed, issues=issues,
                          comments=form.cleaned_data["comments"], reviewer=request.user)
    if form.cleaned_data.get("cut_link"):
        p.cut_link = form.cleaned_data["cut_link"]
    if kind == Review.TL:
        p.status = Project.QC if passed else Project.REVISIONS
    else:
        p.status = Project.QC_PASSED if passed else Project.REVISIONS
    p.save(update_fields=["status", "cut_link"])
    return passed


@roles(TL, ADMIN)
def tl_review(request, pk):
    p = get_object_or_404(Project, pk=pk)
    if request.role == TL and (not request.user.profile.pod or p.editor is None
                               or p.editor.profile.pod_id != request.user.profile.pod_id):
        raise PermissionDenied
    if request.method == "POST":
        passed = _review(request, p, Review.TL)
        messages.success(request, f"{p}: " + ("sent to QC." if passed else "sent back with notes."))
    return redirect("pod")


@roles(QC, ADMIN)
def qc_queue(request):
    queue = Project.objects.filter(status=Project.QC).select_related("agency", "editor__profile", "drive").order_by("deadline")
    sel = queue.filter(pk=request.GET.get("p")).first() or queue.first()
    passed_m = Review.objects.filter(kind="qc", created__date__gte=month_start(timezone.localdate()))
    first_time = None
    if passed_m.exists():
        projs = {r.project_id for r in passed_m}
        ok = sum(1 for pid in projs if not Review.objects.filter(project_id=pid, kind="qc", passed=False).exists())
        first_time = round(ok / len(projs) * 100)
    return render(request, "team/qc.html", {
        "queue": queue, "sel": sel, "checklist": list(enumerate(QC_CHECKLIST)), "form": ReviewForm(),
        "history": sel.reviews.select_related("reviewer__profile") if sel else [],
        "sent_back": Project.objects.filter(status=Project.REVISIONS, reviews__kind="qc").distinct()[:10],
        "passed": Project.objects.filter(reviews__kind="qc", reviews__passed=True).distinct().order_by("-reviews__created")[:10],
        "first_time": first_time})


@roles(QC, ADMIN)
def qc_review(request, pk):
    p = get_object_or_404(Project, pk=pk, status=Project.QC)
    if request.method == "POST":
        passed = _review(request, p, Review.QC)
        messages.success(request, f"{p}: " + ("passed, ready to deliver." if passed else f"sent back to {p.editor.profile.name if p.editor else 'the editor'}."))
    return redirect("qc_queue")


@roles(*EDITING)
def my_payslips(request):
    slips = Payslip.objects.filter(user=request.user, payroll__finalized=True).select_related("payroll")
    return render(request, "team/payslips.html", {"slips": slips})


@roles(*EVERYONE)
def payslip_view(request, pk):
    slip = get_object_or_404(Payslip.objects.select_related("payroll", "user__profile"), pk=pk)
    if request.role != ADMIN and (slip.user_id != request.user.id or not slip.payroll.finalized):
        raise PermissionDenied
    return render(request, "print/payslip.html", {"s": slip})
