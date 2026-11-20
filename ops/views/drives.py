"""Drive desk (and founders): hard drive register and send-backs.
The drive desk never sees projects' money, clients' rates or payroll."""
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from ..audit import record
from ..rbac import ADMIN, has_perm, requires
from ..forms import DriveForm, DriveReturnForm, ReceiveDriveForm, save_sent_back
from ..models import DriveFolder, DriveReturn, HardDrive, Project
from ..services import store_image

__all__ = ["drives", "drive_edit", "drive_returns", "drive_return_status", "drive_delete", "drive_sent_on"]


def _next_code(kind="HDD"):
    nums = [int(c.split("-")[-1]) for c in HardDrive.objects.filter(code__startswith=kind + "-").values_list("code", flat=True)
            if c.split("-")[-1].isdigit()]
    return f"{kind}-{(max(nums) + 1) if nums else 1:02d}"


def _save_photos(form, target):
    for f in form.cleaned_data.get("photos_upload") or []:
        target.photos.add(store_image(f))


@requires("drives.manage")
def drives(request):
    from .suggest import films_summary
    """Drive register: filters and search redraw in place (live.js); the search also
    looks inside folder names, couples and films on each drive."""
    g = request.GET
    qs = HardDrive.objects.select_related("agency").prefetch_related("photos", "folders", "returns", "projects")
    status, q = g.get("status", ""), g.get("q", "").strip()
    f = {"agency": g.get("agency", ""), "kind": g.get("kind", ""), "folders": g.get("folders", ""),
         "sort": g.get("sort", "")}
    if status == "all":
        pass
    elif status in dict(HardDrive.STATUS):
        qs = qs.filter(status=status)
    else:
        status = ""
        qs = qs.exclude(status="sent")
    if f["agency"] == "studio":
        qs = qs.filter(studio_owned=True)
    elif f["agency"].isdigit():
        qs = qs.filter(agency_id=f["agency"])
    if f["kind"] in ("HDD", "SSD", "Card"):
        qs = qs.filter(kind=f["kind"])
    if f["folders"] in (DriveFolder.PENDING, DriveFolder.ACCEPTED, DriveFolder.DECLINED):
        qs = qs.filter(folders__status=f["folders"]).distinct()
    elif f["folders"] == "none":
        qs = qs.filter(folders__isnull=True)
    hits = []
    if q:   # every word must match somewhere ("zara kabir" finds "Zara_Kabir_Wedding")
        hq = Q()
        for w in q.replace("_", " ").split()[:6]:
            qs = qs.filter(Q(code__icontains=w) | Q(contents__icontains=w) | Q(agency__name__icontains=w)
                           | Q(serial__icontains=w) | Q(agency__code__iexact=w) | Q(location__icontains=w)
                           | Q(folders__path__icontains=w) | Q(folders__couple_guess__icontains=w)
                           | Q(projects__couple__icontains=w))
            hq &= (Q(path__icontains=w) | Q(couple_guess__icontains=w) | Q(client__name__icontains=w)
                   | Q(drive__code__icontains=w))
        qs = qs.distinct()
        hits = list(DriveFolder.objects.filter(hq).select_related("drive__agency", "client")
                    .order_by("drive__code", "path")[:60])
    from django.db.models import F
    order = {"newest": "-received_on", "oldest": "received_on", "agency": "agency__name",
             "data_desc": F("data_gb").desc(nulls_last=True), "data_asc": F("data_gb").asc(nulls_last=True)}
    qs = qs.order_by(order.get(f["sort"], "code"), "code")
    view = "list" if g.get("view") == "list" else "cards"
    switch = {}
    for v in ("cards", "list"):
        params = g.copy()
        params.pop("view", None)
        if v == "list":
            params["view"] = "list"
        switch[v] = "?" + params.urlencode()
    form = ReceiveDriveForm(request.POST or None, request.FILES or None, initial={"code": _next_code()})
    if request.method == "POST" and form.is_valid():
        d = form.save()
        _save_photos(form, d)
        from .suggest import link_drive_films
        linked = link_drive_films(request, d)
        if linked:
            messages.info(request, f"{linked} film(s) linked to {d.code}.")
        from .folders import add_folders
        made, _ = add_folders(request, d, form.cleaned_data.get("folder_list", ""))
        record(request, "drive.receive", d.code, f"{made} folder(s)")
        live = request.headers.get("X-Live-Page")
        messages.success(request, f"{d.code} recorded. Write {d.code} on its label."
                         + (f" {made} folder(s) listed; they're waiting on Projects for a decision." if made
                            else " Open it from the list to paste its folders." if live else " You can paste its folders below."))
        return redirect(request.get_full_path()) if live else redirect("drive_edit", d.pk)
    from django.db.models import Count
    by = dict(HardDrive.objects.order_by().values_list("status").annotate(n=Count("id")))
    chips = [("", "In studio", sum(n for k, n in by.items() if k != "sent"))] + \
            [(k, label, by.get(k, 0)) for k, label in HardDrive.STATUS] + [("all", "All", sum(by.values()))]
    drives_list = list(qs)
    for d in drives_list:
        fs = d.folders.all()
        d.n_folders = len(fs)
        d.films_text = films_summary(d.projects.all())
        d.n_waiting = sum(1 for x in fs if x.status == DriveFolder.PENDING)
        rets = sorted(d.returns.all(), key=lambda r: (r.sent_on, r.pk))
        d.last_return = rets[-1] if rets else None
        d.used_pct = (min(100, round(float(d.data_gb) / (float(d.capacity_tb) * 1000) * 100))
                      if d.data_gb and d.capacity_tb else None)
    active = sum(1 for k in ("agency", "kind", "folders") if f[k]) + (1 if q else 0)
    from ..models import Agency
    return render(request, "drives/register.html", {
        "drives": drives_list, "form": form, "chips": chips, "view": view, "switch": switch, "hits": hits, "f": f, "active": active,
        "status": status, "q": q, "statuses": HardDrive.STATUS, "agencies": Agency.objects.order_by("name"),
        "waiting": DriveFolder.objects.filter(status=DriveFolder.PENDING).count(),
        "can_delete": has_perm(request.user, "projects.manage")})


@requires("drives.manage")
def drive_edit(request, pk):
    d = get_object_or_404(HardDrive, pk=pk)
    form = DriveForm(request.POST or None, request.FILES or None, instance=d)
    if request.method == "POST" and form.is_valid():
        form.save()
        _save_photos(form, d)
        from .suggest import link_drive_films
        link_drive_films(request, d)
        on = form.cleaned_data.get("sent_back_on")
        if on:
            _, created = save_sent_back(d, on, request.user)
            if created or "sent_back_on" in form.changed_data:
                record(request, "drive.sent_back", d.code, f"{on:%d %b %Y}")
            if d.open_jobs.exists():
                messages.warning(request, f"{d.code} still has open films on Projects. Check they don't need this footage.")
        messages.success(request, f"{d.code} saved." + (f" Sent back on {on:%-d %b %Y}." if on else ""))
        return redirect(request.get_full_path() if request.headers.get("X-Live-Page") else "drives")
    return render(request, "drives/edit.html", {"d": d, "form": form, "folders": d.folders.select_related("client"),
                                                "can_delete": has_perm(request.user, "projects.manage"),
                                                "jobs": d.projects.select_related("agency").order_by("couple", "deliverable")})


@requires("drives.manage")
def drive_returns(request):
    ready = HardDrive.objects.filter(status__in=("ready", "backup_done")).select_related("agency")
    sel = ready.filter(pk=request.GET.get("d")).first() or ready.first()
    form = None
    if sel:
        open_jobs = sel.open_jobs
        form = DriveReturnForm(request.POST or None, request.FILES or None,
                               initial={"sent_to": f"{sel.agency.name} – {sel.agency.contact_person}" if sel.agency else ""})
        if request.method == "POST" and form.is_valid():
            if open_jobs.exists():
                messages.error(request, f"{sel.code} still has open jobs; it can't go back yet.")
            else:
                r = form.save(commit=False)
                r.drive, r.recorded_by = sel, request.user
                r.save()
                _save_photos(form, r)
                sel.status = "sent"
                sel.save(update_fields=["status"])
                messages.success(request, f"{sel.code} recorded as sent back.")
                return redirect("drive_returns")
    return render(request, "drives/returns.html", {
        "ready": ready, "sel": sel, "form": form,
        "jobs": sel.projects.all() if sel else [], "open_jobs": sel.open_jobs if sel else [],
        "recent": DriveReturn.objects.select_related("drive", "drive__agency")[:12],
        "statuses": DriveReturn.STATUS})


@requires("drives.manage")
def drive_return_status(request, pk):
    r = get_object_or_404(DriveReturn, pk=pk)
    if request.method == "POST" and request.POST.get("status") in dict(DriveReturn.STATUS):
        r.status = request.POST["status"]
        r.save(update_fields=["status"])
    return redirect("drive_returns")


@requires("projects.manage")
def drive_delete(request, pk):
    """Delete a drive entry (founders). Its folder list and send-back records go with it;
    films that were on it stay on Projects, just no longer linked to a drive."""
    d = get_object_or_404(HardDrive, pk=pk)
    if request.method != "POST":
        return redirect("drive_edit", d.pk)
    code, films = d.code, d.projects.count()
    d.delete()
    record(request, "drive.delete", code, f"{films} film(s) unlinked")
    text = f"Deleted {code}." + (f" {films} film(s) stay on Projects without a drive." if films else "")
    if request.headers.get("X-Requested-With") == "fetch":
        from django.http import JsonResponse
        return JsonResponse({"ok": True, "messages": [{"level": "success", "text": text}],
                             "remove": [f'[data-drive-cell="{pk}"]']})
    messages.success(request, text)
    return redirect("drives")


@requires("drives.manage")
def drive_sent_on(request, pk):
    """Set (or correct) the day a drive went back, straight from the register: the drive becomes
    Sent back and the date shows in its Sent back column and history. Answers fetch with the cell."""
    from datetime import date
    from django.http import JsonResponse
    from django.template.loader import render_to_string
    from django.utils import timezone
    d = get_object_or_404(HardDrive.objects.select_related("agency"), pk=pk)
    if request.method != "POST":
        return redirect("drives")
    try:
        on = date.fromisoformat(request.POST.get("sent_on", ""))
    except ValueError:
        on = None
    say = []
    if not on:
        say.append(("error", "Pick the date it was sent back."))
    elif on > timezone.localdate():
        say.append(("error", "That date is in the future. Pick the day it actually went back."))
    elif on < d.received_on:
        say.append(("error", f"That's before {d.code} was received ({d.received_on:%-d %b %Y})."))
    else:
        _, created = save_sent_back(d, on, request.user)
        if d.status != "sent":
            d.status = "sent"
            d.save(update_fields=["status"])
        record(request, "drive.sent_back", d.code, f"{on:%d %b %Y}")
        say.append(("success", f"{d.code} sent back on {on:%-d %b %Y}."))
        if d.open_jobs.exists():
            say.append(("warning", f"{d.code} still has open films on Projects. Check they don't need this footage."))
    bad = any(l == "error" for l, _ in say)
    if request.headers.get("X-Requested-With") == "fetch":
        d.last_return = d.returns.order_by("-sent_on", "-pk").first()
        cell = render_to_string("drives/_sent_cell.html", {"d": d}, request=request)
        return JsonResponse({"ok": not bad, "messages": [{"level": l, "text": t} for l, t in say],
                             "swap": [{"sel": f'[data-sent-cell="{d.pk}"]', "mode": "outer", "html": cell}],
                             "refresh": not bad})
    for l, t in say:
        getattr(messages, l)(request, t)
    return redirect("drives")
