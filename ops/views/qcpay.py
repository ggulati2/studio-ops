"""QC earnings: QC closes a film once they're done with it; a founder sets what each
closed film pays (it differs by agency and film); the QC person sees it month by month.
Founders can also correct or remove QC entries."""
from collections import defaultdict
from datetime import date

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .. import services as S
from ..audit import record
from ..models import FilmEvent, Project, Review
from ..rbac import has_perm, requires
from ..services import add_months, month_end, month_start

__all__ = ["qc_close", "qc_earnings", "qc_fee", "qc_entry", "qc_send"]


def _back(request):
    return S.safe_next(request, request.POST.get("next") or request.META.get("HTTP_REFERER"), reverse("qc_queue"))


def _fetch(request):
    return request.headers.get("X-Requested-With") == "fetch"


@requires("qc.review")
def qc_close(request, pk):
    """Close a film from QC (QC is done with it) or open it again."""
    p = get_object_or_404(Project, pk=pk)
    back = _back(request)
    if request.method != "POST":
        return redirect(back)
    if request.POST.get("op") == "reopen":
        if p.qc_closed_on and (p.qc_closed_by_id == request.user.pk or has_perm(request.user, "payroll.manage")):
            record(request, "qc.reopen", str(p), f"was closed {p.qc_closed_on:%d %b}")
            p.qc_closed_on, p.qc_closed_by = None, None
            p.save(update_fields=["qc_closed_on", "qc_closed_by"])
            messages.success(request, f"{p} is open in QC again.")
    elif not p.reviews.filter(kind=Review.QC).exists():
        messages.error(request, f"{p} hasn't been checked in QC yet, so it can't be closed from QC.")
    elif p.qc_closed_on:
        messages.info(request, f"{p} was already closed from QC on {p.qc_closed_on:%d %b}.")
    else:
        p.qc_closed_on, p.qc_closed_by = timezone.localdate(), request.user
        p.save(update_fields=["qc_closed_on", "qc_closed_by"])
        record(request, "qc.close", str(p), "closed from QC")
        messages.success(request, f"{p} closed from QC. It counts in {p.qc_closed_on:%B}'s earnings"
                                  + ("." if p.qc_fee else " once a founder sets its fee."))
    return redirect(back)


@requires("payroll.manage")
def qc_fee(request, pk):
    """A founder sets what a film pays the QC person."""
    p = get_object_or_404(Project, pk=pk)
    back = _back(request)
    if request.method != "POST":
        return redirect(back)
    raw = (request.POST.get("fee") or "").replace(",", "").replace("₹", "").strip()
    if raw and (not raw.isdigit() or int(raw) > 1_000_000):
        say = [("error", "Enter the QC fee in whole rupees, e.g. 500.")]
    else:
        old, p.qc_fee = p.qc_fee, int(raw or 0)
        if old != p.qc_fee:
            p.save(update_fields=["qc_fee"])
            record(request, "qc.fee", str(p), f"₹{old:,} -> ₹{p.qc_fee:,}")
        say = [("success", f"{p}: QC fee ₹{p.qc_fee:,}.")]
    if _fetch(request):
        return JsonResponse({"ok": not any(l == "error" for l, _ in say),
                             "messages": [{"level": l, "text": t} for l, t in say]})
    for l, t in say:
        getattr(messages, l)(request, t)
    return redirect(back)


@requires("payroll.manage")
def qc_entry(request, pk):
    """Founders correct the QC record of a film: change a review's result or notes, delete a
    review, change who closed it and when, or take the film off the QC list altogether."""
    p = get_object_or_404(Project, pk=pk)
    back = _back(request)
    if request.method != "POST":
        return redirect(back)
    P, op = request.POST, request.POST.get("op")
    if op in ("review_save", "review_delete"):
        r = get_object_or_404(Review, pk=P.get("review"), project=p)
        if op == "review_delete":
            r.delete()
            record(request, "qc.review_delete", str(p), f"{'passed' if r.passed else 'failed'} {r.created:%d %b}")
            left = p.reviews.filter(kind=Review.QC).count()
            msg = f"{p}: that QC entry is deleted."
            if _waiting_for_qc(p):
                msg += (" The film is still waiting for QC, so it stays on the list. To take it off,"
                        " use Remove this film from the QC list.")
            elif left:
                msg += f" {left} more QC entr{'y' if left == 1 else 'ies'} left for this film."
            messages.success(request, msg)
        else:
            r.passed = P.get("passed") == "1"
            r.comments = P.get("comments", "")[:4000]
            r.save(update_fields=["passed", "comments"])
            record(request, "qc.review_edit", str(p), "passed" if r.passed else "failed")
            messages.success(request, f"{p}: QC entry saved.")
    elif op == "closed":
        on = None
        if P.get("qc_closed_on"):
            try:
                on = date.fromisoformat(P["qc_closed_on"])
            except ValueError:
                on = False
        by = P.get("qc_closed_by", "")
        if on is False:
            messages.error(request, "That date isn't valid.")
        else:
            p.qc_closed_on = on or None
            p.qc_closed_by_id = int(by) if by.isdigit() and on else None
            p.save(update_fields=["qc_closed_on", "qc_closed_by"])
            record(request, "qc.closed_edit", str(p), f"{on or 'not closed'}")
            messages.success(request, f"{p}: " + (f"closed from QC on {on:%d %b}." if on else "not closed from QC."))
    elif op == "remove":
        n, was = _remove_from_qc(p)
        record(request, "qc.remove", str(p), f"{n} QC entr{'y' if n == 1 else 'ies'} removed"
                                             + (f"; status {was} -> {p.get_status_display()}" if was else ""))
        msg = f"{p}: removed from the QC list ({n} entr{'y' if n == 1 else 'ies'} deleted)."
        if was:
            msg += f" It was {was}; it's now {p.get_status_display()}, so the editor can send it again when ready."
        messages.success(request, msg)
    return redirect(back)


def _waiting_for_qc(p):
    return p.status == Project.QC or p.events.filter(status=FilmEvent.QC).exists()


def _remove_from_qc(p):
    """Take a film off the QC list for good: its QC entries go, it's no longer closed from QC,
    and if it (or any of its events) was waiting for QC it goes back to the editor, so it
    doesn't come straight back as "Waiting". Queued AutoChecks for it are cancelled.
    Returns (entries deleted, old status label or "")."""
    from ..models import AutoCheckRun
    n = p.reviews.filter(kind=Review.QC).count()
    p.reviews.filter(kind=Review.QC).delete()
    was = p.get_status_display() if _waiting_for_qc(p) else ""
    for e in p.events.filter(status=FilmEvent.QC):
        if e.reviews.exists():
            e.status = FilmEvent.REVISIONS            # it has a TL review history: back with the editor
            e.save(update_fields=["status"])
        else:
            e.delete()                                # sent for QC and nothing else: as if never sent
    p.qc_closed_on, p.qc_closed_by = None, None
    fields = ["qc_closed_on", "qc_closed_by"]
    if p.status == Project.QC:
        p.status = Project.IN_PROGRESS
        fields.append("status")
    p.save(update_fields=fields)
    S.sync_film_status(p)
    p.refresh_from_db(fields=["status", "whole_review"])
    AutoCheckRun.objects.filter(project=p, status__in=(AutoCheckRun.QUEUED, AutoCheckRun.RUNNING)).update(
        status=AutoCheckRun.FAILED, finished_at=timezone.now(), progress="Film removed from QC")
    return n, was


@requires("qc.earnings")
def qc_earnings(request):
    """The QC person's films closed from QC: this month (or any month) and overall."""
    u, today = request.user, timezone.localdate()
    try:
        month = date.fromisoformat((request.GET.get("m") or "") + "-01")
    except ValueError:
        month = month_start(today)
    view = "all" if request.GET.get("v") == "all" else "month"
    mine = Project.objects.filter(qc_closed_by=u).select_related("agency", "editor__profile")
    rounds = dict(Review.objects.filter(reviewer=u, kind=Review.QC).values("project_id")
                  .annotate(n=Count("id")).values_list("project_id", "n"))
    ctx = {"view": view, "month": month, "prev": add_months(month, -1), "next": add_months(month, 1),
           "is_current": month == month_start(today)}
    if view == "month":
        films = list(mine.filter(qc_closed_on__range=(month, month_end(month))).order_by("-qc_closed_on"))
        for p in films:
            p.my_rounds = rounds.get(p.pk, 0)
        ctx.update(films=films, total=sum(p.qc_fee for p in films), pending=sum(1 for p in films if not p.qc_fee))
        # checked by me, not closed yet: one click to close
        open_ids = (Review.objects.filter(reviewer=u, kind=Review.QC).values_list("project_id", flat=True))
        ctx["to_close"] = list(Project.objects.filter(pk__in=open_ids, qc_closed_on__isnull=True)
                               .select_related("agency", "editor__profile").prefetch_related("events").order_by("-id")[:60])
        for p in ctx["to_close"]:
            p.my_rounds = rounds.get(p.pk, 0)
            p.events_left = [e.name for e in p.events.all() if e.status != FilmEvent.PASSED]
    else:
        months = defaultdict(lambda: {"films": 0, "total": 0, "pending": 0})
        for p in Project.objects.filter(qc_closed_by=u).only("qc_closed_on", "qc_fee"):
            m = months[month_start(p.qc_closed_on)]
            m["films"] += 1
            m["total"] += p.qc_fee
            m["pending"] += 0 if p.qc_fee else 1
        rows = [{"month": k, **v} for k, v in sorted(months.items(), reverse=True)]
        ctx.update(months=rows, total=sum(r["total"] for r in rows), films_n=sum(r["films"] for r in rows),
                   pending=sum(r["pending"] for r in rows))
    return render(request, "team/qc_earnings.html", ctx)


def qc_month_summary(month):
    """For founders on the QC page: per QC person, films closed this month and what they earn."""
    out = defaultdict(lambda: {"films": 0, "total": 0, "pending": 0})
    for p in (Project.objects.filter(qc_closed_on__range=(month, month_end(month)), qc_closed_by__isnull=False)
              .select_related("qc_closed_by__profile")):
        r = out[p.qc_closed_by]
        r["films"] += 1
        r["total"] += p.qc_fee
        r["pending"] += 0 if p.qc_fee else 1
    return [{"u": u, **v} for u, v in out.items()]


@requires("projects.manage")
def qc_send(request):
    """A founder sends a film (or one of its events) for QC on the editor's behalf, when they
    forgot to. It goes straight to QC."""
    back = reverse("qc_queue")
    if request.method != "POST":
        return redirect(back)
    P = request.POST
    raw = P.get("project", "")
    p = (Project.objects.filter(Q(status__in=Project.WORKABLE) | Q(status=Project.TL_REVIEW)
                                | Q(status=Project.QC, whole_review=False), pk=raw).first() if raw.isdigit() else None)
    link_raw = P.get("cut_link", "").strip()
    link = S.safe_web_link(link_raw)
    name = " ".join(P.get("event", "").split())[:40].strip()
    name = name[:1].upper() + name[1:]
    if not p:
        messages.error(request, "Pick the film to send for QC.")
        return redirect(back)
    if link_raw and not link:
        messages.error(request, "That review link isn't a web address. Paste the full link starting with https://")
        return redirect(back)
    if name:
        e = p.events.filter(name__iexact=name).first() or FilmEvent(project=p, name=name)
        if e.pk and e.status == FilmEvent.QC:
            messages.info(request, f"{p} · {e.name} is already waiting for QC.")
            return redirect(f"{back}?p={p.pk}")
        e.status, e.sent_on = FilmEvent.QC, timezone.now()
        e.cut_link = link or e.cut_link
        e.save()
        S.note_cut_link(p, e, link, request.user)
        S.sync_film_status(p)
        what = f"{p.couple} · {e.name}"
    else:
        p.status = Project.QC
        p._status_was = None                    # the whole film is sent now (even if events had it In QC)
        p.cut_link = link or p.cut_link
        p.save(update_fields=["status", "cut_link"])
        S.note_cut_link(p, None, link, request.user)
        what = str(p)
    record(request, "qc.send", str(p), f"sent for QC by a founder{' · ' + name if name else ''}")
    messages.success(request, f"{what} sent for QC for {p.editor.profile.name if p.editor_id else 'the editor'}.")
    return redirect(f"{back}?p={p.pk}")
