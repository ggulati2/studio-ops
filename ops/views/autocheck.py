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
from django.db.models import F
from django.utils import timezone

from ..audit import record
from ..forms import StyledMixin
from .. import services as S
from ..models import Agency, AutoCheckRule, AutoCheckRun, FilmEvent, Project
from ..rbac import QC, has_perm, requires, role_of

__all__ = ["autocheck_start", "autocheck_claim", "autocheck_update", "autocheck_report", "autocheck_rules", "qc_helper",
           "qc_helper_zip"]

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
            "rules": AutoCheckRule.for_project(p).as_dict(), "portal": request.build_absolute_uri("/"),
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
    return JsonResponse({"ok": True, "job": None})


@requires("qc.review")
def autocheck_update(request, pk):
    """Progress and the final report, relayed from the extension by the portal page."""
    run = get_object_or_404(AutoCheckRun, pk=pk)
    if request.method != "POST":
        return JsonResponse({"ok": False}, status=405)
    if run.started_by_id != request.user.pk and not has_perm(request.user, "payroll.manage"):
        return JsonResponse({"ok": False}, status=403)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "bad data"}, status=400)
    kind = data.get("type")
    fields = []
    if kind in ("progress", "status"):
        if run.status in (AutoCheckRun.QUEUED, AutoCheckRun.RUNNING):
            run.status = AutoCheckRun.RUNNING
            run.progress = str(data.get("text") or run.progress)[:200]
            fields = ["status", "progress", "updated"]
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
    elif kind == "failed":
        run.status, run.finished_at = AutoCheckRun.FAILED, timezone.now()
        run.progress = ("Stopped: " + str(data.get("error") or "unknown error"))[:200]
        fields = ["status", "finished_at", "progress", "updated"]
    if fields:
        run.save(update_fields=fields)
    return JsonResponse({"ok": True, "status": run.status})


@requires("qc.review")
def autocheck_report(request, pk):
    run = get_object_or_404(AutoCheckRun.objects.select_related("project__agency", "started_by__profile", "event"), pk=pk)
    r = run.report or {}
    return render(request, "team/autocheck_report.html", {
        "run": run, "p": run.project, "rep": r, "comments": r.get("comments", []), "issues": r.get("issues", []),
        "posted": r.get("posted", []), "structure": r.get("structure", []),
        "prev_label": labels(run)[0] or "V1", "cur_label": labels(run)[1]})


class RuleForm(StyledMixin, forms.ModelForm):
    deliverable = forms.ChoiceField(label="Film type", required=False,
                                    choices=[("", "Any film type")] + [(d, d) for d in Project.DELIVERABLES])

    class Meta:
        model = AutoCheckRule
        fields = ["agency", "deliverable", "min_seconds", "max_seconds", "sensitivity", "check_blacks",
                  "check_highlights", "check_black_frames", "check_audio_peaks", "check_loudness", "check_pops",
                  "check_silence", "check_shake", "check_jump_cuts", "check_colour_jumps"]

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
