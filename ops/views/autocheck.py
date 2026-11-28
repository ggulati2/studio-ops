"""AutoCheck: V2 checked against V1's Frame.io comments plus a hygiene pass.

The work runs in the QC person's Chrome through the "Red Wed QC Helper" extension
(Frame.io doesn't let other websites read its comments). This side only starts a run,
stores progress and the report, holds the rules, and hands out the extension."""
import io
import json
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from django import forms
from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db.models import F, Q
from django.utils import timezone

from ..audit import record
from ..forms import StyledMixin
from .. import services as S
from .. import learning as L
from ..models import Agency, AutoCheckLesson, AutoCheckRule, AutoCheckRun, AutoCheckTuning, FilmEvent, Project
from ..rbac import QC, has_perm, requires, role_of

__all__ = ["autocheck_start", "autocheck_claim", "autocheck_update", "autocheck_report", "autocheck_rules", "qc_helper",
           "qc_helper_zip", "autocheck_learnt", "autocheck_home", "autocheck_links", "autocheck_repost"]

HELPER_DIR = Path(settings.BASE_DIR) / "qc_helper"
STALE = timezone.timedelta(minutes=30)      # a running check that hasn't reported for this long has stopped


def helper_version():
    try:
        return json.loads((HELPER_DIR / "manifest.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        return "?"


_frameio = S.frameio_link


def labels(run):
    """What the two versions are called on Frame.io comments: V1/V2 on round 2, V2/V3 on round 3…"""
    if run.round > 1:
        return f"V{run.round - 1}", f"V{run.round}"
    if run.round == 1:
        return "", "V1"
    return "V1", "V2"


def _job(run, request):
    p = run.project
    prev, cur = labels(run)
    return {"id": run.pk, "v1": run.v1_link, "v2": run.v2_link, "film": str(p) + (f" · {run.event.name}" if run.event_id else ""),
            "couple": p.couple, "deliverable": p.deliverable, "agency": p.agency.code if p.agency_id else "",
            "checker": request.user.profile.name if hasattr(request.user, "profile") else request.user.get_username(),
            "rules": {**AutoCheckRule.for_project(p).as_dict(), "tune": L.factors_for(p)},
            "prev_issues": L.prev_issues(run), "portal": request.build_absolute_uri("/"),
            "prev_label": prev or "V1", "cur_label": cur, "auto": run.auto}


@requires("qc.review")
def autocheck_start(request, pk):
    """Run AutoCheck now in this browser: on a film or one event, with the links from its history
    (or ones pasted by QC). A check already queued for it is started here instead of a new one."""
    p = get_object_or_404(Project.objects.select_related("agency"), pk=pk)
    if request.method != "POST":
        return redirect(f"{reverse('qc_queue')}?p={p.pk}")
    ev_raw = request.POST.get("event", "")
    event = get_object_or_404(FilmEvent, pk=ev_raw, project=p) if ev_raw.isdigit() else None
    v1, v2 = request.POST.get("v1_link", ""), request.POST.get("v2_link", "")
    v2c, v1c = _frameio(v2), _frameio(v1) if v1.strip() else ""
    if not v2c:
        return JsonResponse({"ok": False, "error": "The link to check must be a Frame.io link (https://f.io/… or https://next.frame.io/…)."})
    if v1c is None:
        return JsonResponse({"ok": False, "error": "The earlier version's link isn't a Frame.io link."})
    if v1c and v1c == v2c:
        return JsonResponse({"ok": False, "error": "Both links are the same. Each version needs its own Frame.io link."})
    mine = p.autochecks.filter(event=event)
    busy = mine.filter(status=AutoCheckRun.RUNNING, updated__gte=timezone.now() - STALE).first()
    if busy:
        return JsonResponse({"ok": False, "error": "An AutoCheck is already running on this."})
    _, last = S.autocheck_pair(p, event)
    rnd = last.round if last and last.link == v2c else 0
    run = mine.filter(status=AutoCheckRun.QUEUED, v2_link=v2c, v1_link=v1c).first()
    if run and AutoCheckRun.objects.filter(pk=run.pk, status=AutoCheckRun.QUEUED).update(
            status=AutoCheckRun.RUNNING, started_by=request.user, attempts=F("attempts") + 1, progress="Starting…"):
        run.refresh_from_db()
    else:
        run = AutoCheckRun.objects.create(project=p, event=event, round=rnd, v1_link=v1c, v2_link=v2c,
                                          started_by=request.user, status=AutoCheckRun.RUNNING, progress="Starting…")
    record(request, "autocheck.start", str(p), (event.name + " · " if event else "") + ("2 versions" if v1c else "1 version"))
    return JsonResponse({"ok": True, "job": _job(run, request)})


@requires("qc.review")
def autocheck_claim(request):
    """A QC person's browser (with the helper) asks for the next AutoCheck queued by an editor's send.
    One at a time per person; a check that stopped responding is queued again (twice at most)."""
    if request.method != "POST":
        return JsonResponse({"ok": False}, status=405)
    if role_of(request.user) != QC:
        return JsonResponse({"ok": True, "job": None})
    now = timezone.now()
    for r in AutoCheckRun.objects.filter(status=AutoCheckRun.RUNNING, updated__lt=now - STALE):
        if r.auto and r.attempts < 2:
            AutoCheckRun.objects.filter(pk=r.pk).update(status=AutoCheckRun.QUEUED, started_by=None, updated=now,
                                                        progress="Queued again: the last try stopped responding")
        else:
            AutoCheckRun.objects.filter(pk=r.pk).update(status=AutoCheckRun.FAILED, finished_at=now,
                                                        progress="Stopped: the check stopped responding")
    if AutoCheckRun.objects.filter(started_by=request.user, status=AutoCheckRun.RUNNING).exists():
        return JsonResponse({"ok": True, "job": None})
    for r in AutoCheckRun.objects.filter(status=AutoCheckRun.QUEUED, auto=True).order_by("started_at")[:5]:
        if AutoCheckRun.objects.filter(pk=r.pk, status=AutoCheckRun.QUEUED).update(
                status=AutoCheckRun.RUNNING, started_by=request.user, attempts=F("attempts") + 1,
                progress="Starting…", updated=now):
            if AutoCheckRun.objects.filter(started_by=request.user, status=AutoCheckRun.RUNNING).count() > 1:
                # another tab of this person claimed one at the same moment: give this one back
                AutoCheckRun.objects.filter(pk=r.pk).update(status=AutoCheckRun.QUEUED, started_by=None,
                                                            attempts=F("attempts") - 1, progress="Waiting for a QC computer")
                return JsonResponse({"ok": True, "job": None})
            run = AutoCheckRun.objects.select_related("project__agency", "event").get(pk=r.pk)
            return JsonResponse({"ok": True, "job": _job(run, request)})
    return JsonResponse({"ok": True, "job": _feedback_job(request, now)})


FEEDBACK_HELPER = "1.5.0"                      # helpers before this would take a read job for a full check
FB_CLAIM = timezone.timedelta(minutes=10)      # a feedback read not reported back by then is handed out again


def _feedback_job(request, now):
    """Nothing to check: maybe re-read QC's comments on a version checked lately (read only), so
    AutoCheck learns from what QC did there. One at a time per person, about once a day per version."""
    if L._older(request.POST.get("helper", ""), FEEDBACK_HELPER):
        return None
    if AutoCheckRun.objects.filter(fb_claim_by=request.user, fb_claim_at__gte=now - FB_CLAIM).exists():
        return None
    candidates = (AutoCheckRun.objects.filter(status=AutoCheckRun.DONE, finished_at__gte=now - L.READ_FOR,
                                              finished_at__lte=now - L.FIRST_READ)
                  .filter(Q(fb_read_at__isnull=True) | Q(fb_read_at__lt=now - L.EVERY))
                  .filter(Q(fb_claim_at__isnull=True) | Q(fb_claim_at__lt=now - FB_CLAIM))
                  .select_related("project__agency", "event").order_by(F("fb_read_at").asc(nulls_first=True), "finished_at"))
    for run in candidates[:30]:
        if not L.needs_read(run, now):
            continue
        if not AutoCheckRun.objects.filter(pk=run.pk).filter(Q(fb_claim_at__isnull=True) | Q(fb_claim_at__lt=now - FB_CLAIM)).update(
                fb_claim_at=now, fb_claim_by=request.user):
            continue
        p = run.project
        return {"id": run.pk, "feedback": True, "auto": True, "v2": run.v2_link, "cur_label": labels(run)[1],
                "film": str(p) + (f" · {run.event.name}" if run.event_id else ""), "portal": request.build_absolute_uri("/")}
    return None


@requires("qc.review")
def autocheck_update(request, pk):
    """Progress and the final report, relayed from the extension by the portal page."""
    run = get_object_or_404(AutoCheckRun, pk=pk)
    if request.method != "POST":
        return JsonResponse({"ok": False}, status=405)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "bad data"}, status=400)
    kind = data.get("type")
    if kind == "feedback":
        # a daily read of QC's comments, by whoever's helper was handed it (read only, nothing posted)
        if run.fb_claim_by_id != request.user.pk:
            return JsonResponse({"ok": False}, status=403)
        seen = data.get("seen")
        if data.get("error") or not isinstance(seen, list):
            AutoCheckRun.objects.filter(pk=run.pk).update(fb_read_at=timezone.now(), fb_claim_at=None, fb_claim_by=None)
            return JsonResponse({"ok": True, "learnt": False})
        if len(json.dumps(seen)) > 400_000:
            return JsonResponse({"ok": False, "error": "too large"}, status=400)
        learnt = L.read_feedback_safely(run, seen)
        return JsonResponse({"ok": True, "learnt": bool(learnt)})
    if run.started_by_id != request.user.pk and not has_perm(request.user, "payroll.manage"):
        return JsonResponse({"ok": False}, status=403)
    fields = []
    if kind in ("progress", "status"):
        if run.status in (AutoCheckRun.QUEUED, AutoCheckRun.RUNNING):
            run.status = AutoCheckRun.RUNNING
            run.progress = str(data.get("text") or run.progress)[:200]
            fields = ["status", "progress", "updated"]
    elif kind == "done" and (data.get("report") or {}).get("repost"):
        # only the comments that didn't post last time: mark the ones that went up now (and add
        # flags that had never been tried)
        rep = dict(run.report or {})
        rep["posted"] = list(rep.get("posted", []))
        again = {(round(float(x.get("t") or 0), 1), x.get("text")): x for x in data["report"].get("posted", [])}
        for x in rep["posted"]:
            y = again.pop((round(float(x.get("t") or 0), 1), x.get("text")), None)
            if y and not x.get("ok"):
                x["ok"], x["error"] = bool(y.get("ok")), y.get("error") if not y.get("ok") else None
        issue_at = {"[Hygiene] " + (x.get("text") or ""): (i, x.get("kind")) for i, x in enumerate(rep.get("issues", []))}
        for (t, text), y in again.items():
            i, kind = issue_at.get(text, (None, None))
            rep["posted"].append({"t": t, "text": text, "ok": bool(y.get("ok")), "error": y.get("error"), "issue": i, "kind": kind})
        rep["posted"].sort(key=lambda x: float(x.get("t") or 0))
        run.report, run.status = rep, AutoCheckRun.DONE
        run.posted = sum(1 for x in rep.get("posted", []) if x.get("ok"))
        left = sum(1 for x in rep.get("posted", []) if not x.get("ok"))
        c = run.counts
        run.progress = (f"{c['fixed']} fixed · {c['not_fixed']} not fixed · {c['check']} to check · {c['new']} new · "
                        f"{run.posted} posted" + (f" · {left} not posted" if left else ""))[:200]
        fields = ["report", "status", "posted", "progress", "updated"]
    elif kind == "done":
        report = data.get("report") or {}
        if len(json.dumps(report)) > 400_000:
            return JsonResponse({"ok": False, "error": "report too large"}, status=400)
        run.status, run.report, run.finished_at = AutoCheckRun.DONE, report, timezone.now()
        run.posted = int(data.get("posted") or 0)
        run.helper_version = str(report.get("version", ""))[:20]
        c = run.counts
        run.progress = (f"{c['fixed']} fixed · {c['not_fixed']} not fixed · {c['check']} to check · "
                        f"{c['new']} new · {run.posted} posted on V2")
        fields = ["status", "report", "finished_at", "posted", "helper_version", "progress", "updated"]
        run.save(update_fields=fields)
        learnt = L.learn_safely(run)
        if learnt and (learnt["right"] or learnt["wrong"] or learnt["missed"]):
            run.progress = (run.progress + f" · learnt from QC: {learnt['right']} right, {learnt['wrong']} wrong, "
                            f"{learnt['missed']} missed")[:200]
            run.save(update_fields=["progress"])
        fields = []
    elif kind == "failed" and run.status != AutoCheckRun.DONE:     # a finished check stays finished
        run.status, run.finished_at = AutoCheckRun.FAILED, timezone.now()
        run.progress = ("Stopped: " + str(data.get("error") or "unknown error"))[:200]
        fields = ["status", "finished_at", "progress", "updated"]
    if fields:
        run.save(update_fields=fields)
    return JsonResponse({"ok": True, "status": run.status})


def missing_posts(report):
    """Comments that aren't on Frame.io: ones that failed, plus flags never tried (older helpers
    posted 60 at most). Each as {t, text}, in film order."""
    import re
    posted = report.get("posted", []) or []
    out = [{"t": x.get("t"), "text": x.get("text")} for x in posted if not x.get("ok")]
    tried_idx = {x.get("issue") for x in posted if x.get("issue") is not None}
    norm = lambda t: re.sub(r"^\s*\[(autocheck|hygiene)\]\s*(still there from v\d+:\s*)?", "", (t or "").strip(), flags=re.I)[:60].lower()
    tried_text = {norm(x.get("text")) for x in posted}
    for i, x in enumerate(report.get("issues", []) or []):
        if x.get("covered") or i in tried_idx or norm(x.get("text")) in tried_text:
            continue
        out.append({"t": x.get("t"), "text": "[Hygiene] " + (x.get("text") or "")})
    return sorted(out, key=lambda x: float(x.get("t") or 0))


@requires("qc.review")
def autocheck_repost(request, pk):
    """Post again only the comments that didn't go up on Frame.io (runs in this browser)."""
    run = get_object_or_404(AutoCheckRun.objects.select_related("project__agency", "event"), pk=pk)
    if request.method != "POST":
        return redirect("autocheck_report", run.pk)
    missing = missing_posts(run.report or {})
    if not missing:
        return JsonResponse({"ok": False, "error": "Every comment is already posted."})
    if run.status == AutoCheckRun.RUNNING and run.updated >= timezone.now() - STALE:
        return JsonResponse({"ok": False, "error": "This check is still running."})
    run.started_by, run.progress = request.user, f"Posting {len(missing)} comment(s) again…"
    run.save(update_fields=["started_by", "progress", "updated"])
    record(request, "autocheck.repost", str(run.project), f"{len(missing)} comment(s)")
    return JsonResponse({"ok": True, "job": {**_job(run, request), "repost": missing}})


@requires("qc.review")
def autocheck_report(request, pk):
    run = get_object_or_404(AutoCheckRun.objects.select_related("project__agency", "started_by__profile", "event"), pk=pk)
    r = run.report or {}
    return render(request, "team/autocheck_report.html", {
        "run": run, "p": run.project, "rep": r, "comments": r.get("comments", []), "issues": r.get("issues", []),
        "posted": r.get("posted", []), "structure": r.get("structure", []), "learnt": r.get("learnt"),
        "not_posted": len(missing_posts(r)), "helper_version": helper_version(),
        "feedback": r.get("feedback"), "labels": L.LABELS,
        "prev_label": labels(run)[0] or "V1", "cur_label": labels(run)[1]})


class RuleForm(StyledMixin, forms.ModelForm):
    deliverable = forms.ChoiceField(label="Film type", required=False,
                                    choices=[("", "Any film type")] + [(d, d) for d in Project.DELIVERABLES])

    class Meta:
        model = AutoCheckRule
        fields = ["agency", "deliverable", "min_seconds", "max_seconds", "sensitivity", "check_blacks",
                  "check_highlights", "check_black_frames", "check_media_offline", "check_framing", "check_blur",
                  "check_audio_peaks", "check_clipping", "check_loudness", "check_pops", "check_silence",
                  "check_music_end", "check_shake", "check_jump_cuts", "check_colour_jumps"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["agency"].queryset = Agency.objects.filter(active=True)
        self.fields["agency"].empty_label = "Any agency"


@requires("settings.manage")
def autocheck_rules(request):
    edit = AutoCheckRule.objects.filter(pk=request.GET.get("edit") or 0).first()
    if request.method == "POST":
        if request.POST.get("op") == "delete":
            AutoCheckRule.objects.filter(pk=request.POST.get("pk")).delete()
            messages.success(request, "Rule deleted.")
            return redirect("autocheck_rules")
        inst = AutoCheckRule.objects.filter(pk=request.POST.get("pk") or 0).first()
        form = RuleForm(request.POST, instance=inst)
        if form.is_valid():
            dup = AutoCheckRule.objects.filter(agency=form.cleaned_data["agency"],
                                               deliverable=form.cleaned_data["deliverable"]).exclude(pk=inst.pk if inst else None)
            if dup.exists():
                form.add_error(None, "There's already a rule for that agency and film type. Edit that one.")
            else:
                form.save()
                messages.success(request, "AutoCheck rule saved.")
                return redirect("autocheck_rules")
        edit = inst
    else:
        form = RuleForm(instance=edit)
    rules = AutoCheckRule.objects.select_related("agency").order_by("agency__name", "deliverable")
    return render(request, "team/autocheck_rules.html", {"form": form, "rules": rules, "edit": edit,
                                                         "defaults": AutoCheckRule().as_dict()})


@requires("qc.review")
def autocheck_home(request):
    """AutoCheck on its own: check any film (or one of its events) now, see every recent check,
    and get the QC Helper."""
    films = list(Project.objects.select_related("agency").only("id", "couple", "deliverable", "status", "agency__code",
                                                                 "agency__name").order_by("-created")[:600])
    runs = list(AutoCheckRun.objects.select_related("project__agency", "event", "started_by__profile")
                .order_by("-started_at")[:60])
    for r in runs:
        r.prev_label, r.cur_label = labels(r)
        r.prev_label = r.prev_label or "V1"
    busy = {s: sum(1 for r in runs if r.status == s) for s in (AutoCheckRun.QUEUED, AutoCheckRun.RUNNING)}
    pick = Project.objects.filter(pk=request.GET.get("p") or 0).first()
    return render(request, "team/autocheck_home.html", {
        "films": films, "runs": runs, "busy": busy, "version": helper_version(), "helper_version": helper_version(),
        "pick": pick, "is_qc": role_of(request.user) == QC})


@requires("qc.review")
def autocheck_links(request):
    """For the AutoCheck page: a film's events and the two latest versions sent (film or event)."""
    p = get_object_or_404(Project.objects.select_related("agency"), pk=request.GET.get("p") or 0)
    ev_raw = request.GET.get("event", "")
    event = p.events.filter(pk=ev_raw).first() if ev_raw.isdigit() else None
    prev, last = S.autocheck_pair(p, event)
    cur = last.link if last else (event.cut_link if event else p.cut_link) or ""
    return JsonResponse({
        "ok": True, "start": reverse("autocheck_start", args=[p.pk]),
        "events": [{"id": e.pk, "name": e.name, "status": e.get_status_display()} for e in p.events.all()],
        "v1": prev.link if prev and _frameio(prev.link) else "", "v2": cur if _frameio(cur) else "",
        "v1_label": f"V{prev.round}" if prev else "", "v2_label": f"V{last.round}" if last else "",
        "note": ("" if _frameio(cur) else "No Frame.io link sent for this yet: paste the version to check.")})


@requires("settings.manage")
def autocheck_learnt(request):
    """What AutoCheck has learnt from QC: each limit, the evidence, every change (undo / lock)."""
    if request.method == "POST":
        op, P = request.POST.get("op"), request.POST
        row = AutoCheckTuning.objects.filter(pk=P.get("pk") or 0).first()
        if op == "lock" and row:
            row.locked = not row.locked
            row.save(update_fields=["locked", "updated"])
            record(request, "autocheck.tune_lock", str(row), "locked" if row.locked else "unlocked")
            messages.success(request, f"{L.LABELS.get(row.kind, row.kind)}: " + (
                "locked at its current limit." if row.locked else "unlocked. It learns from QC again."))
        elif op == "reset" and row:
            AutoCheckLesson.objects.create(tuning=row, old_factor=row.factor, new_factor=1.0, by=request.user,
                                           reason="Reset to the built-in limit by a founder")
            row.factor, row.samples, row.locked = 1.0, [], False
            row.save(update_fields=["factor", "samples", "locked", "updated"])
            record(request, "autocheck.tune_reset", str(row))
            messages.success(request, f"{L.LABELS.get(row.kind, row.kind)}: back to the built-in limit; learning starts again.")
        elif op == "undo":
            les = AutoCheckLesson.objects.filter(pk=P.get("lesson") or 0, undone=False).select_related("tuning").first()
            if les:
                t = les.tuning
                AutoCheckLesson.objects.create(tuning=t, old_factor=t.factor, new_factor=les.old_factor, by=request.user,
                                               reason="Undone by a founder; the limit is locked so it doesn't come back")
                t.factor, t.locked = les.old_factor, True
                t.save(update_fields=["factor", "locked", "updated"])
                les.undone = True
                les.save(update_fields=["undone"])
                record(request, "autocheck.tune_undo", str(t), f"{les.new_factor} -> {les.old_factor}")
                messages.success(request, f"{L.LABELS.get(t.kind, t.kind)}: change undone and the limit locked. Unlock it to let it learn again.")
        return redirect("autocheck_learnt")
    rows = list(AutoCheckTuning.objects.select_related("agency").exclude(kind__startswith="note:")
                .order_by("kind", "agency__name", "deliverable"))
    for r in rows:
        r.label = L.LABELS.get(r.kind, r.kind)
        judged = r.right + r.wrong
        r.accuracy = round(r.right / judged * 100) if judged else None
        r.active = r.locked or len(r.samples) >= L.MIN_SAMPLES
        r.n = len(r.samples)
    studio = [r for r in rows if r.agency_id is None and not r.deliverable]
    base = {r.kind: r.factor for r in studio}
    # an agency's own limit only matters where it differs from the studio's
    specific = [r for r in rows if not (r.agency_id is None and not r.deliverable) and (r.active or r.locked)
                and (r.locked or abs(r.factor - base.get(r.kind, 1.0)) > 0.005)]
    notes = [(L.NOTES.get(t.kind[5:], t.kind[5:]), t.missed) for t in
             AutoCheckTuning.objects.filter(kind__startswith="note:", agency__isnull=True).order_by("-missed")]
    lessons, same = [], set()
    for les in (AutoCheckLesson.objects.select_related("tuning__agency", "run__project", "by__profile")
                .order_by("-created", F("tuning__agency_id").asc(nulls_first=True), "tuning__deliverable")[:120]):
        # the same step taken studio-wide and for the agency in one run is shown once (studio-wide first)
        key = (les.tuning.kind, les.run_id, les.old_factor, les.new_factor, les.by_id)
        if les.run_id and key in same:
            continue
        same.add(key)
        les.label = L.LABELS.get(les.tuning.kind, les.tuning.kind)
        lessons.append(les)
        if len(lessons) >= 40:
            break
    return render(request, "team/autocheck_learnt.html", {
        "studio": studio, "specific": specific, "notes": notes, "lessons": lessons, "min_samples": L.MIN_SAMPLES,
        "untouched": [v for k, v in L.LABELS.items() if k not in {r.kind for r in studio}]})


@requires("qc.review")
def qc_helper(request):
    return render(request, "team/qc_helper.html", {"version": helper_version()})


@requires("qc.review")
def qc_helper_zip(request):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(HELPER_DIR.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                z.write(f, f"RedWed-QC-Helper/{f.name}")
    resp = HttpResponse(buf.getvalue(), content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="RedWed-QC-Helper-{helper_version()}.zip"'
    return resp
