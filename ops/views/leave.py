"""Advance leave: an editor applies, the founders approve or decline. Approval turns each
working day into a Leave (or Half day) entry, which the planner, calendars, attendance and
payroll already understand."""
from datetime import timedelta

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .. import services as S
from ..audit import record
from ..forms import LeaveRequestForm
from ..models import DayLog, LeaveRequest, PlanEntry, Project
from ..rbac import requires

__all__ = ["my_leave", "leave_cancel", "leave_requests", "leave_decide"]


def _span(r):
    return f"{r.start:%-d %b}" + (f"–{r.end:%-d %b}" if r.end != r.start else "") + (" (half day)" if r.half_day else "")


@requires("work.log_own")
def my_leave(request):
    form = LeaveRequestForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        r = form.save(commit=False)
        r.user = request.user
        r.save()
        record(request, "leave.requested", request.user.profile.name if hasattr(request.user, "profile") else request.user.username,
               f"{_span(r)}: {r.reason}")
        messages.success(request, f"Leave for {_span(r)} sent to the founders. You'll see their answer here.")
        return redirect("my_leave")
    mine = request.user.leave_requests.select_related("decided_by__profile")
    return render(request, "team/leave.html", {"form": form, "mine": mine[:30],
                                               "working_days": {r.pk: len(S.leave_working_days(r.user, r.start, r.end)) for r in mine[:30]}})


@requires("work.log_own")
def leave_cancel(request, pk):
    r = get_object_or_404(LeaveRequest, pk=pk, user=request.user, status=LeaveRequest.PENDING)
    if request.method == "POST":
        r.status = LeaveRequest.CANCELLED
        r.save(update_fields=["status"])
        record(request, "leave.cancelled", str(request.user), _span(r))
        messages.success(request, "Request withdrawn.")
    return redirect("my_leave")


def _clashes(r):
    """What the founders should weigh: who else is off, the person's deadlines and planned work."""
    others = (LeaveRequest.objects.filter(status__in=(LeaveRequest.PENDING, LeaveRequest.APPROVED), start__lte=r.end, end__gte=r.start)
              .exclude(pk=r.pk).exclude(user=r.user).select_related("user__profile"))
    off_logs = (DayLog.objects.filter(date__range=(r.start, r.end), day_type__in=(DayLog.LEAVE, DayLog.HALF))
                .exclude(user=r.user).select_related("user__profile"))
    off = sorted({o.user.profile.name for o in others} | {l.user.profile.name for l in off_logs})
    deadlines = (Project.objects.filter(editor=r.user, deadline__range=(r.start - timedelta(days=1), r.end + timedelta(days=1)))
                 .exclude(status=Project.CLOSED).select_related("agency"))
    planned = PlanEntry.objects.filter(user=r.user, date__range=(r.start, r.end), project__isnull=False).select_related("project__agency")
    return {"off": off, "deadlines": list(deadlines), "planned": sorted({str(e.project) for e in planned})}


@requires("leave.approve")
def leave_requests(request):
    pending = list(LeaveRequest.objects.filter(status=LeaveRequest.PENDING).select_related("user__profile").order_by("start"))
    for r in pending:
        r.clash = _clashes(r)
        r.working = len(S.leave_working_days(r.user, r.start, r.end))
    today = timezone.localdate()
    upcoming = LeaveRequest.objects.filter(status=LeaveRequest.APPROVED, end__gte=today).select_related("user__profile", "decided_by__profile").order_by("start")
    decided = (LeaveRequest.objects.exclude(status=LeaveRequest.PENDING).exclude(status=LeaveRequest.APPROVED, end__gte=today)
               .select_related("user__profile", "decided_by__profile")[:40])
    return render(request, "founder/leave_requests.html", {"pending": pending, "upcoming": upcoming, "decided": decided, "today": today})


@requires("leave.approve")
def leave_decide(request, pk):
    r = get_object_or_404(LeaveRequest.objects.select_related("user__profile"), pk=pk)
    if request.method != "POST":
        return redirect("leave_requests")
    note = request.POST.get("founder_note", "")[:200]
    who = r.user.profile.name
    decision = request.POST.get("decision")
    if decision == "approve" and r.status == LeaveRequest.PENDING:
        n = S.approve_leave(r, request.user, note)
        record(request, "leave.approved", who, f"{_span(r)}; {n} day(s) marked")
        messages.success(request, f"Approved {who}'s leave, {_span(r)}. {n} working day{'s' if n != 1 else ''} marked on the calendar; planned work on those days was cleared.")
    elif decision == "decline" and r.status == LeaveRequest.PENDING:
        r.status, r.decided_by, r.decided_on, r.founder_note = LeaveRequest.DECLINED, request.user, timezone.now(), note
        r.save()
        record(request, "leave.declined", who, f"{_span(r)}{'; ' + note if note else ''}")
        messages.success(request, f"Declined {who}'s leave, {_span(r)}.")
    elif decision == "revoke" and r.status == LeaveRequest.APPROVED and r.end >= timezone.localdate():
        S.undo_leave(r)
        r.status, r.decided_by, r.decided_on = LeaveRequest.CANCELLED, request.user, timezone.now()
        if note:
            r.founder_note = note
        r.save()
        record(request, "leave.revoked", who, _span(r))
        messages.success(request, f"Cancelled {who}'s approved leave, {_span(r)}. The calendar days were cleared.")
    return redirect("leave_requests")
