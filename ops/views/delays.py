"""Delays: editors report that a film will miss its deadline; founders approve a new
date or decline; everything stays on record for tracking."""
from collections import Counter
from datetime import date, timedelta

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .. import services as S
from ..audit import record
from ..forms import DelayForm
from ..models import DelayReport, Project
from ..rbac import requires

__all__ = ["job_delay", "delays", "delay_decide", "delay_log", "client_revision"]


@requires("work.log_own")
def job_delay(request, pk):
    """Editor: tell the founders this job will be late, with a new date and the reason."""
    p = get_object_or_404(Project, pk=pk, editor=request.user)
    if not p.is_open or not p.deadline:
        messages.error(request, "This job has no open deadline to move.")
        return redirect("my_jobs")
    pending = p.delays.filter(status=DelayReport.PENDING).first()
    form = DelayForm(request.POST or None, instance=pending, project=p)
    if request.method == "POST" and form.is_valid():
        d = form.save(commit=False)
        d.project, d.reported_by, d.deadline_then = p, request.user, p.deadline
        d.save()
        record(request, "delay.reported", str(p), f"{p.deadline} -> {d.proposed_date}; {d.get_reason_display()}")
        messages.success(request, "Delay reported. The founders will confirm the new date; you'll see it on this job.")
        return redirect("my_jobs")
    return render(request, "team/delay_form.html", {"form": form, "p": p, "pending": pending,
                                                    "history": p.delays.exclude(pk=getattr(pending, "pk", None))})


@requires("projects.manage")
def delays(request):
    today = timezone.localdate()
    pending = DelayReport.objects.filter(status=DelayReport.PENDING).select_related("project__agency", "project__editor__profile", "reported_by__profile")
    # Late with no word from the editor
    silent = (Project.objects.filter(deadline__lt=today).exclude(status__in=(Project.CLOSED, Project.ON_HOLD, Project.SENT_AGENCY))
              .exclude(delays__status=DelayReport.PENDING).select_related("agency", "editor__profile"))
    since = today - timedelta(days=90)
    recent = DelayReport.objects.filter(reported_on__date__gte=since).select_related("project__editor__profile")
    by_reason = Counter(d.get_reason_display() for d in recent)
    by_editor = Counter(d.project.editor.profile.name for d in recent if d.project.editor_id)
    slipped = [d.days_slip for d in recent if d.status == DelayReport.APPROVED]
    closed = Project.objects.filter(status=Project.CLOSED, closed_on__gte=since, deadline__isnull=False)
    on_time_orig = sum(1 for p in closed if p.timing_override in ("on_time", "client_delay")
                       or (not p.timing_override and (p.first_delivered_on or p.closed_on) <= (p.original_deadline or p.deadline)))
    stats = {"count": recent.count(), "approved": len(slipped), "avg_slip": round(sum(slipped) / len(slipped), 1) if slipped else 0,
             "on_time_pct": round(100 * on_time_orig / closed.count()) if closed.count() else None,
             "silent": silent.count()}
    return render(request, "founder/delays.html", {
        "pending": pending, "silent": silent, "stats": stats, "by_reason": by_reason.most_common(),
        "by_editor": by_editor.most_common(), "today": today,
        "log": DelayReport.objects.exclude(status=DelayReport.PENDING).select_related(
            "project__agency", "project__editor__profile", "decided_by__profile")[:40]})


@requires("projects.manage")
def delay_decide(request, pk):
    d = get_object_or_404(DelayReport, pk=pk, status=DelayReport.PENDING)
    if request.method != "POST":
        return redirect("delays")
    p = d.project
    note = request.POST.get("founder_note", "")[:200]
    if request.POST.get("decision") == "approve":
        try:
            new = date.fromisoformat(request.POST.get("approved_date") or d.proposed_date.isoformat())
        except ValueError:
            new = d.proposed_date
        d.status, d.approved_date = DelayReport.APPROVED, new
        if not p.original_deadline:
            p.original_deadline = p.deadline
        p.deadline = new
        p.save(update_fields=["deadline", "original_deadline"])
        made, short = S.auto_plan(p)
        messages.success(request, f"New deadline for {p}: {new:%-d %b}." + (f" {made} day(s) added to the Planner." if made else ""))
    else:
        d.status = DelayReport.DECLINED
        messages.info(request, f"Delay declined for {p}. The deadline stays {p.deadline:%-d %b}.")
    d.decided_by, d.decided_on, d.founder_note = request.user, timezone.now(), note
    d.save()
    record(request, f"delay.{d.status}", str(p), f"{d.deadline_then} -> {d.approved_date or d.proposed_date}; {note}")
    return redirect("delays")


@requires("projects.manage")
def delay_log(request, pk):
    """Founder records a delay directly (e.g. the client asked for more time)."""
    p = get_object_or_404(Project, pk=pk)
    form = DelayForm(request.POST or None, project=p)
    if request.method == "POST" and form.is_valid():
        d = form.save(commit=False)
        d.project, d.reported_by, d.deadline_then = p, request.user, p.deadline
        d.status, d.approved_date, d.decided_by, d.decided_on = DelayReport.APPROVED, d.proposed_date, request.user, timezone.now()
        d.save()
        if not p.original_deadline:
            p.original_deadline = p.deadline
        p.deadline = d.proposed_date
        p.save(update_fields=["deadline", "original_deadline"])
        S.auto_plan(p)
        record(request, "delay.approved", str(p), f"{d.deadline_then} -> {d.proposed_date}; recorded by founder")
        messages.success(request, f"Deadline moved to {p.deadline:%-d %b} and the delay is on record.")
        return redirect("project_edit", pk=p.pk)
    return render(request, "team/delay_form.html", {"form": form, "p": p, "founder": True, "history": p.delays.all()})


@requires("projects.manage")
def client_revision(request, pk):
    """Reopen a delivered film because the client asked for changes. The first delivery
    date (and whether it was on time) is kept; the revision gets its own due date."""
    from ..forms import ClientRevisionForm
    from ..models import ClientRevision
    p = get_object_or_404(Project, pk=pk, status=Project.CLOSED)
    form = ClientRevisionForm(request.POST or None, project=p)
    if request.method == "POST" and form.is_valid():
        r = form.save(commit=False)
        r.project, r.created_by = p, request.user
        r.round = (p.client_revisions.count() or 0) + 1
        r.save()
        p.status, p.deadline = Project.CLIENT_REV, r.due_date
        if r.editor_id:
            p.editor_id = r.editor_id
        p.save(update_fields=["status", "deadline", "editor"])
        made, _ = S.auto_plan(p)
        record(request, "project.client_revision", str(p), f"round {r.round}, due {r.due_date}")
        messages.success(request, f"{p} reopened for client revision {r.round}, due {r.due_date:%-d %b}. "
                                  "It still counts as delivered on its first date." + (f" {made} day(s) planned." if made else ""))
        return redirect("project_edit", pk=p.pk)
    return render(request, "founder/client_revision.html", {"form": form, "p": p})
